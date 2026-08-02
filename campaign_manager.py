# ---------------------------------------------------------
# campaign_manager.py
#
# Production-facing layer on top of marketing_engine.build_graph().
# A single compiled graph, with a MemorySaver checkpointer, tracks
# many campaigns at once — one LangGraph "thread" per campaign_id.
# Threads are fully independent, so:
#
#   - Business adds a brand new product/campaign -> start_campaign()
#     with a fresh campaign_id. Runs alongside any others already
#     in flight; nothing about existing campaigns is touched.
#
#   - Business wants to change something about a campaign that's
#     already running (new product details, revised goal, bigger
#     budget) -> reroute_campaign() with the SAME campaign_id. This
#     merges the new info into that thread's saved state and sets
#     force_replan=True, so on the next run the evaluator sends it
#     straight back through master_planner_node instead of picking
#     up the stale plan.
#
# Swap MemorySaver for a persistent checkpointer (e.g. SqliteSaver /
# PostgresSaver) in production so campaign state survives restarts.
# ---------------------------------------------------------

import atexit
import threading
from contextlib import ExitStack
from typing import Any, Dict, Optional

from langgraph.checkpoint.postgres import PostgresSaver

from marketing_engine import MarketingState, build_graph_with_checkpointer

import campaign_registry
import campaign_events
import config


class CampaignManager:
    """
    Uses PostgresSaver so campaign state (and therefore each campaign's
    running/reroute history) survives process restarts and is shared
    correctly across multiple workers/servers, not just one Python process.

    Requires POSTGRES_CONN_STRING to be set, e.g.:
        export POSTGRES_CONN_STRING="postgresql://user:password@host:5432/dbname?sslmode=require"

    Needs the 'langgraph-checkpoint-postgres' and 'psycopg[binary,pool]'
    packages installed (see requirements_addition.txt).
    """

    def __init__(self, conn_string: Optional[str] = None):
        conn_string = conn_string or config.postgres_conn_string()

        # PostgresSaver.from_conn_string() returns a context manager that
        # owns the connection pool; we keep it open for the manager's
        # lifetime via an ExitStack and close it cleanly on exit.
        self._exit_stack = ExitStack()
        self._checkpointer: PostgresSaver = self._exit_stack.enter_context(
            PostgresSaver.from_conn_string(conn_string)
        )
        # Creates the checkpoint tables on first run; safe/no-op if they
        # already exist.
        self._checkpointer.setup()

        self._graph = build_graph_with_checkpointer(self._checkpointer)
        self._running: Dict[str, threading.Thread] = {}

        atexit.register(self._exit_stack.close)

    def close(self) -> None:
        """Closes the underlying Postgres connection pool."""
        self._exit_stack.close()

    # -----------------------------------------------------
    # Public API
    # -----------------------------------------------------

    def start_campaign(self, campaign_id: str, brief: Dict[str, Any]) -> None:
        """
        Kicks off a brand new campaign in the background. Safe to call
        while other campaigns are mid-flight — each runs on its own
        thread_id and doesn't share mutable state with the others.
        """
        campaign_registry.register(campaign_id)
        campaign_events.log_event(
            campaign_id, "campaign_started", payload={k: v for k, v in brief.items()}, source="api"
        )

        initial_state: MarketingState = {
            "campaign_id": campaign_id,
            "business_context": brief["business_context"],
            "campaign_goal": brief["campaign_goal"],
            "target_audience": brief["target_audience"],
            "total_budget": brief["total_budget"],
            "user_plan": brief.get("user_plan", "free"),
            "business_website_url": brief.get("business_website_url"),
            "facebook_page_url": brief.get("facebook_page_url"),
            "calendar_id": brief.get("calendar_id"),
            "reference_images": {},
            "force_replan": False,
            "logs": [],
        }
        self._run_in_background(campaign_id, initial_state)

    def reroute_campaign(self, campaign_id: str, updates: Dict[str, Any]) -> None:
        """
        Injects new information (new product, revised goal, new budget,
        etc.) into a campaign that may already be running or already
        has a completed plan, and forces the planner to re-run.

        `updates` can include any subset of: business_context,
        campaign_goal, target_audience, total_budget,
        business_website_url, facebook_page_url.
        """
        current = self._graph.get_state({"configurable": {"thread_id": campaign_id}})
        if current is None or not current.values:
            raise ValueError(
                f"No existing campaign found for campaign_id={campaign_id!r}; "
                f"use start_campaign() for new campaigns."
            )

        merged_state = dict(current.values)
        merged_state.update(updates)
        merged_state["force_replan"] = True
        merged_state["campaign_id"] = campaign_id

        self._run_in_background(campaign_id, merged_state)
        campaign_events.log_event(
            campaign_id, "campaign_rerouted", payload={"updates": updates}, source="api"
        )

    def get_status(self, campaign_id: str) -> Optional[Dict[str, Any]]:
        """Returns the latest known state for a campaign, or None if unknown."""
        snapshot = self._graph.get_state({"configurable": {"thread_id": campaign_id}})
        return dict(snapshot.values) if snapshot and snapshot.values else None

    def is_running(self, campaign_id: str) -> bool:
        thread = self._running.get(campaign_id)
        return thread is not None and thread.is_alive()

    def list_campaign_ids(self) -> list[str]:
        """Every campaign_id ever started, used by tasks.py's daily publish job."""
        return campaign_registry.list_ids()

    def submit_reference_image(
        self, campaign_id: str, asset_id: str, image_bytes: bytes
    ) -> Dict[str, Any]:
        """
        Business supplies a reference photo for one specific asset that
        image_generation_router flagged as pending (see
        pending_reference_requests in MarketingState / _needs_reference_photo
        in marketing_engine.py). Regenerates just that one asset via
        image-to-image and merges the result back into the campaign's
        state — WITHOUT forcing a full replan of the rest of the campaign.
        """
        from image_clients import generate_image

        current = self.get_status(campaign_id)
        if not current:
            raise ValueError(f"No campaign found for campaign_id={campaign_id!r}")

        plan = current.get("campaign_plan", {})
        asset_desc = None
        for assets in plan.values():
            for a in assets:
                candidate_id = f"{a.replace(' ', '_').lower()}"
                if candidate_id in asset_id:
                    asset_desc = a
                    break
            if asset_desc:
                break
        if asset_desc is None:
            raise ValueError(f"asset_id={asset_id!r} not found in this campaign's plan")

        prompt = (
            f"Create a professional marketing image based on the attached "
            f"reference photo.\n"
            f"Business: {current.get('business_context', '')}\n"
            f"Target Audience: {current.get('target_audience', '')}\n"
            f"Asset Type: {asset_desc}\n"
            f"Style: High-end, modern, scroll-stopping social media ad. "
            f"Preserve the actual product/subject shown in the reference photo."
        )

        user_plan = current.get("user_plan") or "free"
        model = "gemini_free" if user_plan != "paid" else "stable_diffusion"
        result = generate_image(prompt=prompt, model_preference=model, reference_image=image_bytes)

        images = dict(current.get("generated_images", {}))
        images[asset_id] = result

        reference_images = dict(current.get("reference_images", {}))
        reference_images[asset_id] = "provided"

        pending = [a for a in current.get("pending_reference_requests", []) if a != asset_id]

        campaign_events.log_event(
            campaign_id, "reference_photo_uploaded", payload={"asset_id": asset_id}, source="api"
        )

        self._graph.update_state(
            {"configurable": {"thread_id": campaign_id}},
            {
                "generated_images": images,
                "reference_images": reference_images,
                "pending_reference_requests": pending,
            },
        )
        return result

    # -----------------------------------------------------
    # Internal execution
    # -----------------------------------------------------

    def _run_in_background(self, campaign_id: str, state: Dict[str, Any]) -> None:
        thread = threading.Thread(
            target=self._execute,
            args=(campaign_id, state),
            daemon=True,
        )
        self._running[campaign_id] = thread
        thread.start()

    def _execute(self, campaign_id: str, state: Dict[str, Any]) -> None:
        config = {"configurable": {"thread_id": campaign_id}}
        try:
            for _ in self._graph.stream(state, config=config):
                pass  # each checkpoint is persisted automatically; poll get_status() for progress
        except Exception as e:
            print(f"[CampaignManager] campaign_id={campaign_id} failed: {e}")


# -----------------------------------------------------
# Example usage
# -----------------------------------------------------

if __name__ == "__main__":
    manager = CampaignManager()

    # Business kicks off their first campaign
    manager.start_campaign(
        "acme-launch",
        {
            "business_context": "Acme Co. sells eco-friendly water bottles.",
            "campaign_goal": "Drive 500 sales of the new steel bottle line.",
            "target_audience": "Environmentally conscious millennials.",
            "total_budget": 3000.0,
            "business_website_url": "https://example.com",
            "facebook_page_url": "https://facebook.com/acmeco",
        },
    )

    # While that's still running, the business launches a second, unrelated
    # campaign for a different product — totally independent thread.
    manager.start_campaign(
        "acme-holiday-promo",
        {
            "business_context": "Acme Co. is running a holiday gift bundle promo.",
            "campaign_goal": "Sell 1000 gift bundles before Dec 20.",
            "target_audience": "Gift shoppers aged 25-45.",
            "total_budget": 4000.0,
        },
    )

    # Later, the business adds a new product to the first campaign — this
    # reroutes "acme-launch" (forces a fresh plan) without touching
    # "acme-holiday-promo" at all.
    manager.reroute_campaign(
        "acme-launch",
        {
            "business_context": (
                "Acme Co. sells eco-friendly water bottles. NEW: just launched "
                "an insulated steel tumbler line alongside the original bottles."
            ),
            "total_budget": 4500.0,
        },
    )
