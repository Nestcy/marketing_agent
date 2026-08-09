# ---------------------------------------------------------
# campaign_manager.py
#
# Two independent gates, both explicit and human-driven — nothing here
# auto-advances past either one:
#
#   PLAN gate:  start_campaign() -> plan_status="draft" -> approve_plan()
#               sets "approved" (cron/day-generation only runs once
#               approved), or refine_plan() regenerates the draft using
#               accumulated feedback.
#
#   DAY gate:   generate_day_asset() produces one day's draft ->
#               asset_status[date]="awaiting_approval" -> approve_day()
#               actually publishes, or tweak_day() regenerates that one
#               day using accumulated feedback (does NOT publish).
#
# Uses PostgresSaver so state survives restarts and is shared correctly
# across multiple workers, not just one Python process.
# ---------------------------------------------------------

import atexit
import threading
from contextlib import ExitStack
from typing import Any, Dict, List, Optional

from langgraph.checkpoint.postgres import PostgresSaver

from marketing_engine import (
    MarketingState,
    build_graph_with_checkpointer,
    build_replan_graph_with_checkpointer,
    generate_daily_asset,
)

import campaign_registry
import campaign_events
import campaign_preferences
import config


class CampaignManager:
    def __init__(self, conn_string: Optional[str] = None):
        conn_string = conn_string or config.postgres_conn_string()

        self._exit_stack = ExitStack()
        self._checkpointer: PostgresSaver = self._exit_stack.enter_context(
            PostgresSaver.from_conn_string(conn_string)
        )
        self._checkpointer.setup()

        self._graph = build_graph_with_checkpointer(self._checkpointer)
        self._replan_graph = build_replan_graph_with_checkpointer(self._checkpointer)
        self._running: Dict[str, threading.Thread] = {}

        atexit.register(self._exit_stack.close)

    def close(self) -> None:
        self._exit_stack.close()

    # -----------------------------------------------------
    # PLAN gate
    # -----------------------------------------------------

    def start_campaign(self, campaign_id: str, brief: Dict[str, Any]) -> None:
        """
        Kicks off research + initial calendar draft in the background.
        Result lands in plan_status="draft" — does NOT start generating
        any daily assets. Call approve_plan() once the owner signs off.
        """
        campaign_registry.register(campaign_id)
        campaign_events.log_event(campaign_id, "campaign_started", payload=dict(brief), source="api")

        initial_state: MarketingState = {
            "campaign_id": campaign_id,
            "business_context": brief["business_context"],
            "campaign_goal": brief["campaign_goal"],
            "target_audience": brief["target_audience"],
            "timeframe_days": brief.get("timeframe_days", 30),
            "user_plan": brief.get("user_plan", "free"),
            "auto_generate_buffer_days": brief.get("auto_generate_buffer_days", 1),
            "business_website_url": brief.get("business_website_url"),
            "facebook_page_url": brief.get("facebook_page_url"),
            "reference_images": {},
            "generated_captions": {},
            "generated_images": {},
            "asset_status": {},
            "logs": [],
        }
        self._run_planning_in_background(campaign_id, initial_state)

    def approve_plan(self, campaign_id: str) -> None:
        """Locks in the draft calendar. Daily generation (cron or on-demand) only proceeds once plan_status == 'approved'."""
        current = self.get_status(campaign_id)
        if not current:
            raise ValueError(f"No campaign found for campaign_id={campaign_id!r}")
        if not current.get("strategy_outline"):
            raise ValueError("Cannot approve a plan that hasn't been generated yet.")

        self._graph.update_state({"configurable": {"thread_id": campaign_id}}, {"plan_status": "approved"})
        campaign_events.log_event(campaign_id, "plan_approved", payload={}, source="api")

    def refine_plan(self, campaign_id: str, feedback: str) -> None:
        """
        Appends feedback to the campaign's learned preferences, then
        regenerates the WHOLE calendar with those preferences applied.
        Stays in plan_status="draft" — needs another approve_plan() call.
        """
        current = self.get_status(campaign_id)
        if not current:
            raise ValueError(f"No campaign found for campaign_id={campaign_id!r}")

        campaign_preferences.add_preference(campaign_id, feedback)
        campaign_events.log_event(campaign_id, "plan_refined", payload={"feedback": feedback}, source="api")

        self._run_replan_in_background(campaign_id, current)

    # -----------------------------------------------------
    # DAY gate
    # -----------------------------------------------------

    def generate_day_asset(self, campaign_id: str, date: str) -> Dict[str, Any]:
        """
        Generates (or regenerates) the caption + image for ONE day.
        Only proceeds if plan_status == "approved". Leaves the day in
        asset_status="awaiting_approval" (or "pending_generation" if it
        needs a reference photo that hasn't been supplied) — never
        publishes.
        """
        current = self.get_status(campaign_id)
        if not current:
            raise ValueError(f"No campaign found for campaign_id={campaign_id!r}")
        if current.get("plan_status") != "approved":
            raise ValueError(f"Plan for campaign_id={campaign_id!r} is not approved yet.")

        result = generate_daily_asset(current, date)

        calendar_plan = dict(current.get("calendar_plan") or {})
        calendar_plan.update(result.get("calendar_plan", {}))
        captions = dict(current.get("generated_captions") or {})
        captions.update(result.get("generated_captions", {}))
        images = dict(current.get("generated_images") or {})
        images.update(result.get("generated_images", {}))
        statuses = dict(current.get("asset_status") or {})
        statuses.update(result.get("asset_status", {}))

        self._graph.update_state(
            {"configurable": {"thread_id": campaign_id}},
            {"calendar_plan": calendar_plan, "generated_captions": captions, "generated_images": images, "asset_status": statuses},
        )

        event_type = "images_generated" if statuses.get(date) == "awaiting_approval" else "reference_photo_requested"
        campaign_events.log_event(campaign_id, event_type, payload={"date": date}, source="cron")

        return {"date": date, "status": statuses.get(date), "caption": captions.get(date), "image": images.get(date)}

    def generate_days_ahead(self, campaign_id: str, count: int, source: str = "api") -> List[Dict[str, Any]]:
        """
        Generates the next `count` NOT-YET-GENERATED days ahead of
        schedule, in order — e.g. a business can call this with count=5
        to get a week's worth of drafts ready to review all at once,
        rather than waiting one-per-day for the cron. Each day still
        goes through the exact same generate_day_asset() call and lands
        at the exact same "awaiting_approval" gate — this only changes
        WHEN generation happens, not the review/approval mechanism.

        Skips any date that's already been generated (present in
        asset_status), regardless of its current status — this never
        clobbers an existing draft or something already approved.
        Stops early if it runs out of remaining un-generated days in
        the campaign's timeframe.
        """
        current = self.get_status(campaign_id)
        if not current:
            raise ValueError(f"No campaign found for campaign_id={campaign_id!r}")
        if current.get("plan_status") != "approved":
            raise ValueError(f"Plan for campaign_id={campaign_id!r} is not approved yet.")

        calendar_dates = sorted(current.get("calendar_dates") or [])
        already_generated = set((current.get("asset_status") or {}).keys())
        candidate_dates = [d for d in calendar_dates if d not in already_generated][:count]

        results = []
        for date in candidate_dates:
            try:
                result = self.generate_day_asset(campaign_id, date)
                results.append(result)
            except Exception as e:
                results.append({"date": date, "status": "error", "error": str(e)})

        campaign_events.log_event(
            campaign_id, "days_generated_ahead",
            payload={"requested": count, "dates": [r["date"] for r in results]},
            source=source,
        )
        return results

    def approve_day(self, campaign_id: str, date: str) -> Dict[str, Any]:
        """
        Marks one day's draft as approved/ready. This platform doesn't
        crosspost yet (that's a separate feature to be added on the
        frontend side) — approval just means "this ad is finished and
        ready for the business to use," not "published somewhere."
        """
        current = self.get_status(campaign_id)
        if not current:
            raise ValueError(f"No campaign found for campaign_id={campaign_id!r}")

        if (current.get("calendar_plan") or {}).get(date) is None:
            raise ValueError(f"No calendar entry for date={date!r}")

        status = (current.get("asset_status") or {}).get(date)
        if status != "awaiting_approval":
            raise ValueError(f"Day {date!r} is not awaiting approval (status={status!r}) — generate it first.")

        statuses = dict(current.get("asset_status") or {})
        statuses[date] = "approved"

        self._graph.update_state({"configurable": {"thread_id": campaign_id}}, {"asset_status": statuses})
        campaign_events.log_event(campaign_id, "asset_approved", payload={"date": date}, source="api")

        return {
            "date": date,
            "status": "approved",
            "caption": (current.get("generated_captions") or {}).get(date),
            "image": (current.get("generated_images") or {}).get(date),
        }

    def tweak_day(self, campaign_id: str, date: str, feedback: str) -> Dict[str, Any]:
        """
        Appends feedback to learned preferences, then regenerates ONLY
        this one day. Does NOT publish — stays at "awaiting_approval"
        for another review.
        """
        campaign_preferences.add_preference(campaign_id, feedback)
        campaign_events.log_event(campaign_id, "asset_tweaked", payload={"date": date, "feedback": feedback}, source="api")
        return self.generate_day_asset(campaign_id, date)

    def submit_reference_image(self, campaign_id: str, date: str, image_bytes: bytes) -> Dict[str, Any]:
        """
        Business supplies a real reference photo for a day that needs
        one. Regenerates that day's image via image-to-image and marks
        it awaiting_approval.
        """
        from image_clients import generate_image

        current = self.get_status(campaign_id)
        if not current:
            raise ValueError(f"No campaign found for campaign_id={campaign_id!r}")

        day_plan = (current.get("calendar_plan") or {}).get(date)
        if day_plan is None:
            raise ValueError(
                f"date={date!r} hasn't been generated yet — a day's specifics are only "
                f"decided when it's generated, so generate this day first (generate_day_asset) "
                f"before uploading a reference photo for it."
            )

        prompt = (
            f"Create a professional social media image based on the attached reference photo.\n"
            f"Business: {current.get('business_context', '')}\n"
            f"Idea: {day_plan['idea']}\n"
            f"Preserve the actual product/subject shown in the reference photo."
        )
        user_plan = current.get("user_plan") or "free"
        model = "gemini_free" if user_plan != "paid" else "stable_diffusion"
        result = generate_image(prompt=prompt, model_preference=model, reference_image=image_bytes)
        result["is_placeholder"] = False

        images = dict(current.get("generated_images") or {})
        images[date] = result
        reference_images = dict(current.get("reference_images") or {})
        reference_images[date] = "provided"
        statuses = dict(current.get("asset_status") or {})
        statuses[date] = "awaiting_approval"

        self._graph.update_state(
            {"configurable": {"thread_id": campaign_id}},
            {"generated_images": images, "reference_images": reference_images, "asset_status": statuses},
        )
        campaign_events.log_event(campaign_id, "reference_photo_uploaded", payload={"date": date}, source="api")
        return result

    # -----------------------------------------------------
    # Reads
    # -----------------------------------------------------

    def get_status(self, campaign_id: str) -> Optional[Dict[str, Any]]:
        snapshot = self._graph.get_state({"configurable": {"thread_id": campaign_id}})
        return dict(snapshot.values) if snapshot and snapshot.values else None

    def is_running(self, campaign_id: str) -> bool:
        thread = self._running.get(campaign_id)
        return thread is not None and thread.is_alive()

    def list_campaign_ids(self) -> list[str]:
        return campaign_registry.list_ids()

    # -----------------------------------------------------
    # Internal execution
    # -----------------------------------------------------

    def _run_planning_in_background(self, campaign_id: str, state: Dict[str, Any]) -> None:
        thread = threading.Thread(target=self._execute_planning, args=(campaign_id, state), daemon=True)
        self._running[campaign_id] = thread
        thread.start()

    def _execute_planning(self, campaign_id: str, state: Dict[str, Any]) -> None:
        cfg = {"configurable": {"thread_id": campaign_id}}
        try:
            for _ in self._graph.stream(state, config=cfg):
                pass
        except Exception as e:
            print(f"[CampaignManager] planning for campaign_id={campaign_id} failed: {e}")

    def _run_replan_in_background(self, campaign_id: str, state: Dict[str, Any]) -> None:
        thread = threading.Thread(target=self._execute_replan, args=(campaign_id, state), daemon=True)
        self._running[campaign_id] = thread
        thread.start()

    def _execute_replan(self, campaign_id: str, state: Dict[str, Any]) -> None:
        cfg = {"configurable": {"thread_id": campaign_id}}
        try:
            for _ in self._replan_graph.stream(state, config=cfg):
                pass
        except Exception as e:
            print(f"[CampaignManager] replan for campaign_id={campaign_id} failed: {e}")
