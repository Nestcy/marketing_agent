import datetime
from typing import TypedDict, List, Dict, Any, Optional
import operator
from typing import Annotated

from langgraph.graph import StateGraph, END

# ---------------------------------------------------------
# 1. STATE DEFINITION
# ---------------------------------------------------------

class MarketingState(TypedDict):
    # Identity — lets the same graph track many independent, concurrently
    # running campaigns (see campaign_manager.py)
    campaign_id: Optional[str]

    business_context: str
    campaign_goal: str
    target_audience: str
    total_budget: float

    # "free" or "paid" — gates which image model tier gets used, and
    # whether unresolved reference-image requests block that asset.
    # Defaults to "free" if not supplied.
    user_plan: Optional[str]

    # Optional enrichment sources — never required to run the pipeline
    business_website_url: Optional[str]
    facebook_page_url: Optional[str]
    website_context: Optional[str]
    facebook_context: Optional[str]

    # Autonomous research via Brave Search + Firecrawl (business_research_node)
    research_notes: Optional[str]

    # Set to True to force the planner to run again on an already-planned
    # campaign (e.g. business added a new product or a new campaign brief
    # while a previous plan for this campaign_id was already executing)
    force_replan: Optional[bool]

    # Planners will populate this
    campaign_plan: Optional[Dict[str, Any]]
    budget_allocations: Optional[Dict[str, float]]

    # Generation outputs
    generated_copy: Optional[Dict[str, str]]
    generated_images: Optional[Dict[str, Dict[str, str]]]  # e.g. {"ig_post_1": {"model": "gemini_flash_image", "local_path": "..."}}

    # Which asset_ids need a business-supplied reference photo before they
    # can be generated (e.g. "actual product shot", "founder photo"), and
    # which of those have been supplied. Populated by image_generation_router,
    # filled in via CampaignManager.submit_reference_image().
    pending_reference_requests: Optional[List[str]]
    reference_images: Optional[Dict[str, str]]  # asset_id -> "provided" once supplied

    # Google/ISO calendar id to schedule publish events against, plus the
    # resulting asset_id -> ISO date map built by calendar_scheduling_node
    calendar_id: Optional[str]
    publish_schedule: Optional[Dict[str, str]]

    # Final statuses
    publishing_status: Optional[Dict[str, str]]

    # System logs
    logs: Annotated[List[str], operator.add]


# ---------------------------------------------------------
# 2. CONTEXT GATHERING & RESEARCH NODES (optional, non-blocking)
# ---------------------------------------------------------

def business_context_gatherer_node(state: MarketingState):
    """
    Enriches business_context with live info pulled from the business's
    website and/or Facebook Page, when provided. Neither source is
    required — a missing URL or a failed fetch just means the agents
    plan with whatever context they were given directly.
    """
    from context_gatherer import fetch_website_context, fetch_facebook_context

    log_lines = ["[Context Gatherer] Checking for website/Facebook enrichment..."]

    website_context = None
    facebook_context = None

    website_url = state.get("business_website_url")
    if website_url:
        website_context = fetch_website_context(website_url)
        log_lines.append(
            f" \u2705 Website context pulled from {website_url}" if website_context
            else f" \u26a0\ufe0f Could not pull website context from {website_url} (continuing without it)"
        )
    else:
        log_lines.append(" - No website URL provided, skipping.")

    facebook_url = state.get("facebook_page_url")
    if facebook_url:
        facebook_context = fetch_facebook_context(facebook_url)
        log_lines.append(
            f" \u2705 Facebook Page context pulled from {facebook_url}" if facebook_context
            else f" \u26a0\ufe0f Could not pull Facebook context from {facebook_url} (continuing without it)"
        )
    else:
        log_lines.append(" - No Facebook Page URL provided, skipping.")

    return {
        "website_context": website_context,
        "facebook_context": facebook_context,
        "logs": log_lines,
    }


def business_research_node(state: MarketingState):
    """
    Autonomous research step, separate from the direct-URL enrichment
    above. Uses Brave Search (general web search — reviews, competitors,
    recent news) and Firecrawl (deep-scrapes the business's own site if
    a URL was given, or searches+scrapes if not) to ground the plan in
    real, current information rather than just the text the business
    typed into the campaign brief.

    Both MCP calls fail soft — a missing BRAVE_API_KEY/FIRECRAWL_API_KEY
    or a down MCP server just means research_notes stays empty and the
    planner works with less context, same pattern as context_gatherer.py.
    """
    from mcp_clients import brave_web_search, firecrawl_scrape, firecrawl_search

    log_lines = ["[Research] Researching business via Brave Search + Firecrawl..."]

    business_summary = state.get("business_context", "")[:200]
    search_query = f"{business_summary} reviews competitors"

    search_results = brave_web_search(search_query)
    log_lines.append(
        " \u2705 Brave Search results retrieved" if search_results
        else " \u26a0\ufe0f Brave Search unavailable or returned nothing (check BRAVE_API_KEY)"
    )

    website_url = state.get("business_website_url")
    if website_url:
        crawled = firecrawl_scrape(website_url)
        log_lines.append(
            f" \u2705 Firecrawl scraped {website_url}" if crawled
            else f" \u26a0\ufe0f Firecrawl could not scrape {website_url} (check FIRECRAWL_API_KEY)"
        )
    else:
        crawled = firecrawl_search(business_summary)
        log_lines.append(
            " \u2705 Firecrawl search results retrieved (no site URL given, searched instead)" if crawled
            else " \u26a0\ufe0f Firecrawl search unavailable or returned nothing"
        )

    research_notes = "\n\n".join(p for p in [search_results, crawled] if p) or None
    return {"research_notes": research_notes, "logs": log_lines}


def _full_business_context(state: MarketingState) -> str:
    """Combines the raw business_context with any enrichment we managed to pull."""
    parts = [state.get("business_context", "")]
    if state.get("website_context"):
        parts.append(f"[From business website]\n{state['website_context']}")
    if state.get("facebook_context"):
        parts.append(f"[From Facebook Page]\n{state['facebook_context']}")
    if state.get("research_notes"):
        parts.append(f"[From web research]\n{state['research_notes']}")
    return "\n\n".join(p for p in parts if p)


# ---------------------------------------------------------
# 3. PLANNING NODES
# ---------------------------------------------------------

def replanning_evaluator_node(state: MarketingState):
    """Evaluates if the current context requires a new plan."""
    state['logs'].append("[Evaluator] Checking business context...")
    needs_plan = True if (not state.get('campaign_plan') or state.get('force_replan')) else False
    return {"logs": [f"[Evaluator] Needs new plan: {needs_plan}"]}


_PLANNER_SYSTEM_PROMPT = """You are a senior performance marketing strategist.
Given a business, a campaign goal, a target audience, and a total budget,
produce a 4-week paid social/search campaign plan and a budget split across
facebook_ads, tiktok_ads, and google_ads.

Respond ONLY with a JSON object in this exact shape, no extra commentary:
{
  "campaign_plan": {
    "week_1": ["<asset description>", "<asset description>"],
    "week_2": ["<asset description>", "<asset description>"],
    "week_3": ["<asset description>", "<asset description>"],
    "week_4": ["<asset description>", "<asset description>"]
  },
  "budget_allocations": {
    "facebook_ads": <float>,
    "tiktok_ads": <float>,
    "google_ads": <float>
  }
}

Rules:
- Every asset is a still image (this platform generates images only, not
  video) — describe each asset as an image concept, e.g. "Product Showcase
  Image", "Customer Testimonial Graphic", "Lifestyle Photo".
- If an asset should be based on a real photo the business would need to
  supply (an actual product shot, a founder's photo, real store/team
  imagery) rather than a purely AI-imagined scene, say so explicitly in
  the description, e.g. "Product Showcase Image (needs product reference
  photo)" — downstream routing looks for phrases like "reference photo"
  or "needs photo" to know when to pause and ask the business for one.
- budget_allocations values must sum to the total_budget provided.
- Tailor asset ideas to the business context, goal, and audience — avoid
  generic filler. Use any research notes/website/Facebook context provided
  to ground ideas in the business's real products, tone, and positioning.
"""


def master_planner_node(state: MarketingState):
    """Generates the multi-week campaign strategy and budget distribution via Groq."""
    state['logs'].append("[Planner] Generating comprehensive campaign strategy...")

    user_prompt = (
        f"Business: {_full_business_context(state)}\n"
        f"Campaign goal: {state['campaign_goal']}\n"
        f"Target audience: {state['target_audience']}\n"
        f"Total budget: ${state['total_budget']:.2f}"
    )

    try:
        from llm_client import generate_structured
        from schemas import PlannerOutput

        result: PlannerOutput = generate_structured(_PLANNER_SYSTEM_PROMPT, user_prompt, PlannerOutput)
        result.validate_budget_matches(state["total_budget"])  # raises if allocations don't add up

        return {
            "campaign_plan": result.campaign_plan.model_dump(),
            "budget_allocations": result.budget_allocations.model_dump(),
            "force_replan": False,
            "logs": ["[Planner] LLM-generated 4-week plan and budgets established (validated)."],
        }
    except Exception as e:
        mock_plan = {
            "week_1": ["Brand Awareness Image", "Product Showcase Image (needs product reference photo)"],
            "week_2": ["Customer Testimonial Graphic", "Lifestyle Image"],
            "week_3": ["UGC Style Image", "Promo Image"],
            "week_4": ["Retargeting Image", "Urgency Image"],
        }
        mock_budgets = {
            "facebook_ads": state['total_budget'] * 0.5,
            "tiktok_ads": state['total_budget'] * 0.3,
            "google_ads": state['total_budget'] * 0.2,
        }
        return {
            "campaign_plan": mock_plan,
            "budget_allocations": mock_budgets,
            "force_replan": False,
            "logs": [f"[Planner] LLM call failed ({e}); used fallback mock plan."],
        }


_IDEATION_SYSTEM_PROMPT = """You are a senior social media copywriter.
Given a campaign plan (a dict of week -> list of asset descriptions), plus
business context and target audience, write short, scroll-stopping ad copy
for each asset.

Respond ONLY with a JSON object in this exact shape, no extra commentary:
{
  "generated_copy": {
    "<asset_id>": "<copy text>",
    ...
  }
}

Rules:
- asset_id should be formatted as "<week_key>_<asset_description_lowercase_with_underscores>",
  e.g. "week_1_product_showcase_image".
- Keep each copy line under 220 characters, platform-appropriate (emoji ok, no hashtags spam).
- Write one copy entry per asset in the plan.
"""


def content_ideation_node(state: MarketingState):
    """Generates the text copy and prompts for the media assets via Groq."""
    state['logs'].append("[Ideation] Generating ad copy and media prompts...")

    plan = state.get("campaign_plan", {})
    user_prompt = (
        f"Business: {_full_business_context(state)}\n"
        f"Target audience: {state['target_audience']}\n"
        f"Campaign plan: {plan}"
    )

    try:
        from llm_client import generate_structured
        from schemas import IdeationOutput

        result: IdeationOutput = generate_structured(_IDEATION_SYSTEM_PROMPT, user_prompt, IdeationOutput)
        return {"generated_copy": result.generated_copy, "logs": ["[Ideation] LLM-generated copy created (validated)."]}
    except Exception as e:
        mock_copy = {
            "week_1_brand_awareness_image": "Discover our new collection! \U0001f680",
            "week_1_product_showcase_image_(needs_product_reference_photo)": "See it up close. \u2728",
        }
        return {
            "generated_copy": mock_copy,
            "logs": [f"[Ideation] LLM call failed ({e}); used fallback mock copy."],
        }


# ---------------------------------------------------------
# 4. GENERATION ROUTING NODE (images only)
# ---------------------------------------------------------

_SD_KEYWORDS = {"product", "showcase", "lifestyle", "photorealistic", "fashion", "photo", "model"}
_REFERENCE_NEEDED_PHRASES = ("reference photo", "needs photo", "needs product reference")


def _pick_image_model(asset_description: str, user_plan: str) -> str:
    """
    Free-tier users always route to Gemini's free-tier image model.
    Paid-tier users get routed to a task-appropriate paid model, same
    heuristic as before.
    """
    if user_plan != "paid":
        return "gemini_free"

    lower = asset_description.lower()
    if any(kw in lower for kw in _SD_KEYWORDS):
        return "stable_diffusion"
    return "dalle3"


def _needs_reference_photo(asset_description: str) -> bool:
    lower = asset_description.lower()
    return any(phrase in lower for phrase in _REFERENCE_NEEDED_PHRASES)


def _build_image_prompt(asset_description: str, state: MarketingState) -> str:
    return (
        f"Create a professional marketing image for the following campaign.\n"
        f"Business: {_full_business_context(state)}\n"
        f"Target Audience: {state['target_audience']}\n"
        f"Asset Type: {asset_description}\n"
        f"Style: High-end, modern, scroll-stopping social media ad.\n"
        f"Do NOT include any text or watermarks in the image."
    )


def image_generation_router(state: MarketingState):
    """
    Generates one image per planned asset. Assets whose description
    signals they need a real business-supplied photo (see
    _REFERENCE_NEEDED_PHRASES) are skipped here and added to
    pending_reference_requests instead of being blindly AI-imagined —
    they get generated later via CampaignManager.submit_reference_image()
    once the business uploads a photo, optionally after a Celery
    notification task nudges them to do so (see tasks.py).
    """
    from image_clients import generate_image

    plan = state.get("campaign_plan", {})
    user_plan = state.get("user_plan") or "free"
    reference_images = state.get("reference_images") or {}

    images: Dict[str, Dict[str, str]] = {}
    pending: List[str] = []
    log_lines: List[str] = ["[Image Router] Routing image requests..."]

    for week_key, assets in plan.items():
        for asset_desc in assets:
            asset_id = f"{week_key}_{asset_desc.replace(' ', '_').lower()}"

            if _needs_reference_photo(asset_desc) and asset_id not in reference_images:
                pending.append(asset_id)
                log_lines.append(f" \u23f8\ufe0f {asset_id} awaiting business-supplied reference photo — skipped for now")
                continue

            model = _pick_image_model(asset_desc, user_plan)
            prompt = _build_image_prompt(asset_desc, state)
            try:
                result = generate_image(prompt=prompt, model_preference=model)
                images[asset_id] = result
                log_lines.append(
                    f" \u2705 {asset_id} \u2192 {model} | "
                    f"path={result.get('local_path', result.get('url', 'n/a'))}"
                )
            except Exception as e:
                images[asset_id] = {"model": model, "url": None, "error": str(e)}
                log_lines.append(f" \u26a0\ufe0f {asset_id} \u2192 {model} FAILED: {e}")

    log_lines.append(
        f"[Image Router] Finished. {len(images)} image(s) generated, "
        f"{len(pending)} awaiting reference photo(s)."
    )
    return {"generated_images": images, "pending_reference_requests": pending, "logs": log_lines}


# ---------------------------------------------------------
# 5. CALENDAR SCHEDULING NODE
# ---------------------------------------------------------

def calendar_scheduling_node(state: MarketingState):
    """
    Turns the week_1..week_4 asset plan into a concrete daily publish
    schedule (one asset per day, starting today) and creates a calendar
    event per asset via the Calendar MCP server, if configured.

    publish_schedule (asset_id -> ISO date) is what tasks.py's daily
    Celery task reads to decide which assets are due to actually go
    live "today" — this node just builds the schedule and (optionally)
    puts it on a human-visible calendar; it doesn't publish anything
    itself.
    """
    from mcp_clients import calendar_create_event

    plan = state.get("campaign_plan", {})
    calendar_id = state.get("calendar_id") or "primary"
    log_lines = ["[Calendar] Building daily publish schedule..."]

    schedule: Dict[str, str] = {}
    start_date = datetime.date.today()
    day_offset = 0

    for week_key, assets in plan.items():
        for asset_desc in assets:
            asset_id = f"{week_key}_{asset_desc.replace(' ', '_').lower()}"
            publish_date = start_date + datetime.timedelta(days=day_offset)
            schedule[asset_id] = publish_date.isoformat()

            event_id = calendar_create_event(
                calendar_id=calendar_id,
                summary=f"Publish: {asset_desc}",
                description=(
                    f"Auto-scheduled by Marketing Engine for campaign "
                    f"{state.get('campaign_id', 'unknown')}"
                ),
                start_iso=f"{publish_date.isoformat()}T09:00:00",
                end_iso=f"{publish_date.isoformat()}T09:30:00",
            )
            if event_id:
                log_lines.append(f" \u2705 Calendar event created for {asset_id} on {publish_date.isoformat()}")

            day_offset += 1  # one asset published per day, in plan order

    log_lines.append(f"[Calendar] {len(schedule)} asset(s) scheduled across {day_offset} day(s).")
    return {"publish_schedule": schedule, "logs": log_lines}


# ---------------------------------------------------------
# 6. EXECUTION NODES
# ---------------------------------------------------------

def ad_platform_publisher(state: MarketingState):
    """
    First-run publish — pushes whatever's generated so far live (in
    PAUSED/draft state, per publisher_clients.py) immediately after
    planning. The recurring daily publish of newly-due assets from
    publish_schedule is handled separately by tasks.py's Celery beat
    task, not here.
    """
    from publisher_clients import publish_to_all_platforms

    log_lines = ["[Publisher] Pushing initial campaign to Facebook, TikTok, Google..."]
    campaign_name = f"Campaign - {state.get('campaign_goal', 'Growth')}"
    budgets = state.get("budget_allocations", {})
    images = state.get("generated_images", {})

    status = publish_to_all_platforms(
        campaign_name=campaign_name,
        budget_allocations=budgets,
        images=images,
    )

    for platform, info in status.items():
        st = info.get("status", "PAUSED")
        b = info.get("budget_allocated_usd", 0.0)
        log_lines.append(f" \u2705 [{platform.upper()}] Status: {st} | Budget: ${b:.2f}")

    log_lines.append("[Publisher] Campaigns and budgets deployed.")
    return {"publishing_status": status, "logs": log_lines}


# ---------------------------------------------------------
# 7. GRAPH DEFINITION & ROUTING LOGIC
# ---------------------------------------------------------

def should_plan(state: MarketingState):
    """Conditional edge logic: route to planner if needed, else skip to ideation."""
    if not state.get('campaign_plan') or state.get('force_replan'):
        return "master_planner_node"
    return "content_ideation_node"


def _add_nodes_and_edges(workflow: StateGraph) -> None:
    workflow.add_node("context_gatherer", business_context_gatherer_node)
    workflow.add_node("business_research_node", business_research_node)
    workflow.add_node("evaluator", replanning_evaluator_node)
    workflow.add_node("master_planner_node", master_planner_node)
    workflow.add_node("content_ideation_node", content_ideation_node)
    workflow.add_node("image_generation_router", image_generation_router)
    workflow.add_node("calendar_scheduling_node", calendar_scheduling_node)
    workflow.add_node("ad_platform_publisher", ad_platform_publisher)

    workflow.set_entry_point("context_gatherer")
    workflow.add_edge("context_gatherer", "business_research_node")
    workflow.add_edge("business_research_node", "evaluator")

    workflow.add_conditional_edges(
        "evaluator",
        should_plan,
        {
            "master_planner_node": "master_planner_node",
            "content_ideation_node": "content_ideation_node",
        },
    )

    workflow.add_edge("master_planner_node", "content_ideation_node")
    workflow.add_edge("content_ideation_node", "image_generation_router")
    workflow.add_edge("image_generation_router", "calendar_scheduling_node")
    workflow.add_edge("calendar_scheduling_node", "ad_platform_publisher")
    workflow.add_edge("ad_platform_publisher", END)


def build_graph():
    """
    Note: build_graph() alone gives you a single-run graph, same as before.
    For production use where multiple campaigns for the same business run
    concurrently, and a running campaign can be rerouted mid-flight when a
    new product/campaign brief comes in, compile with a checkpointer and
    a per-campaign thread_id — see campaign_manager.py.
    """
    workflow = StateGraph(MarketingState)
    _add_nodes_and_edges(workflow)
    return workflow.compile()


def build_graph_with_checkpointer(checkpointer):
    """
    Same graph as build_graph(), but compiled with a checkpointer so state
    is persisted per thread_id (= campaign_id). This is what makes it safe
    to run many campaigns concurrently and to reroute a running one — see
    campaign_manager.py.
    """
    workflow = StateGraph(MarketingState)
    _add_nodes_and_edges(workflow)
    return workflow.compile(checkpointer=checkpointer)


# ---------------------------------------------------------
# 8. MOCK EXECUTION (For testing)
# ---------------------------------------------------------

if __name__ == "__main__":
    app = build_graph()

    initial_state = {
        "campaign_id": "demo-1",
        "business_context": "We are a SaaS startup launching a new AI tool. We need aggressive growth.",
        "campaign_goal": "Acquire 1000 new signups this month.",
        "target_audience": "Tech founders, marketers, and developers.",
        "total_budget": 5000.0,
        "user_plan": "free",
        "business_website_url": None,
        "facebook_page_url": None,
        "force_replan": False,
        "logs": [],
    }

    print("--- STARTING WORKFLOW ---")
    for output in app.stream(initial_state):
        for key, value in output.items():
            print(f"Node: {key}")
            if "logs" in value:
                print(value["logs"][-1])
            print("---")

    print("\nWorkflow Complete!")
