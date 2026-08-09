import datetime
from typing import TypedDict, List, Dict, Any, Optional
import operator
from typing import Annotated

from langgraph.graph import StateGraph, END

# ---------------------------------------------------------
# 1. STATE DEFINITION
# ---------------------------------------------------------

class MarketingState(TypedDict):
    campaign_id: Optional[str]

    business_context: str
    campaign_goal: str
    target_audience: str
    timeframe_days: int  # 30 or 90, business chooses

    user_plan: Optional[str]  # "free" or "paid" — gates image model tier

    business_website_url: Optional[str]
    facebook_page_url: Optional[str]
    website_context: Optional[str]
    facebook_context: Optional[str]
    research_notes: Optional[str]

    # Plan-level gate
    calendar_plan: Optional[Dict[str, Dict[str, Any]]]  # date -> {idea, platform, needs_reference_photo}
    plan_status: Optional[str]  # "draft" | "approved"

    # Per-day asset state (populated incrementally, one day at a time —
    # NOT all generated up front)
    generated_captions: Optional[Dict[str, str]]      # date -> caption
    generated_images: Optional[Dict[str, Dict[str, str]]]  # date -> {model, local_path, url}
    asset_status: Optional[Dict[str, str]]            # date -> "pending_generation" | "awaiting_approval" | "approved" | "published"
    reference_images: Optional[Dict[str, str]]        # date -> "provided"

    logs: Annotated[List[str], operator.add]


# ---------------------------------------------------------
# 2. CONTEXT GATHERING & RESEARCH NODES (optional, non-blocking)
# ---------------------------------------------------------

def business_context_gatherer_node(state: MarketingState):
    from context_gatherer import fetch_website_context, fetch_facebook_context

    log_lines = ["[Context Gatherer] Checking for website/Facebook enrichment..."]
    website_context = None
    facebook_context = None

    website_url = state.get("business_website_url")
    if website_url:
        website_context = fetch_website_context(website_url)
        log_lines.append(
            f" \u2705 Website context pulled from {website_url}" if website_context
            else f" \u26a0\ufe0f Could not pull website context from {website_url}"
        )
    else:
        log_lines.append(" - No website URL provided, skipping.")

    facebook_url = state.get("facebook_page_url")
    if facebook_url:
        facebook_context = fetch_facebook_context(facebook_url)
        log_lines.append(
            f" \u2705 Facebook Page context pulled from {facebook_url}" if facebook_context
            else f" \u26a0\ufe0f Could not pull Facebook context from {facebook_url}"
        )
    else:
        log_lines.append(" - No Facebook Page URL provided, skipping.")

    return {"website_context": website_context, "facebook_context": facebook_context, "logs": log_lines}


def business_research_node(state: MarketingState):
    """Autonomous research via Tavily (search) + Firecrawl (scrape/search) — direct REST, no MCP."""
    from research_clients import tavily_search, firecrawl_scrape, firecrawl_search

    log_lines = ["[Research] Researching business via Tavily + Firecrawl..."]
    business_summary = state.get("business_context", "")[:200]

    search_results = tavily_search(f"{business_summary} reviews competitors")
    log_lines.append(
        " \u2705 Tavily search results retrieved" if search_results
        else " \u26a0\ufe0f Tavily unavailable or returned nothing (check TAVILY_API_KEY)"
    )

    website_url = state.get("business_website_url")
    if website_url:
        crawled = firecrawl_scrape(website_url)
        log_lines.append(
            f" \u2705 Firecrawl scraped {website_url}" if crawled
            else f" \u26a0\ufe0f Firecrawl could not scrape {website_url}"
        )
    else:
        crawled = firecrawl_search(business_summary)
        log_lines.append(
            " \u2705 Firecrawl search results retrieved" if crawled
            else " \u26a0\ufe0f Firecrawl search unavailable or returned nothing"
        )

    research_notes = "\n\n".join(p for p in [search_results, crawled] if p) or None
    return {"research_notes": research_notes, "logs": log_lines}


def _full_business_context(state: MarketingState) -> str:
    parts = [state.get("business_context", "")]
    if state.get("website_context"):
        parts.append(f"[From business website]\n{state['website_context']}")
    if state.get("facebook_context"):
        parts.append(f"[From Facebook Page]\n{state['facebook_context']}")
    if state.get("research_notes"):
        parts.append(f"[From web research]\n{state['research_notes']}")
    return "\n\n".join(p for p in parts if p)


# ---------------------------------------------------------
# 3. PLANNER — produces the full day-by-day calendar, then STOPS
#    for owner approval. Does not generate any images/captions yet.
# ---------------------------------------------------------

_PLANNER_SYSTEM_PROMPT = """You are a senior organic social media strategist.
Given a business, a campaign goal, a target audience, and a timeframe in
days, produce a day-by-day content calendar — one post idea per day.

Respond ONLY with the structured output requested: a LIST of day
entries, one per date, each with its own date field set to the exact
date string it corresponds to. For each entry:
- date: the exact date string this entry is for (from the list of dates given below)
- idea: a short, specific content concept (not generic filler)
- platform: which single platform this post is best suited for
  (instagram, facebook, or tiktok)
- needs_reference_photo: true if the idea requires a REAL photo the
  business would need to supply (an actual product shot, a founder's
  photo, real store/team imagery) rather than a purely AI-imagined
  scene; false if it can be fully AI-generated.

Vary content types across the calendar — product highlights, behind
the scenes, testimonials, educational/tips content, promotions,
community/engagement posts — don't repeat the same idea pattern every
day. Ground ideas in the business's real context, tone, and any
research notes provided. If a "Learned preferences" section is given,
follow every point in it.
"""


_CALENDAR_CHUNK_SIZE = 20  # days per LLM call — keeps each tool-call response small/reliable
                            # rather than risking one giant 90-entry call


def _fallback_pattern_for_dates(dates: List[str]) -> Dict[str, Dict[str, Any]]:
    pattern = [
        {"idea": "Product highlight post", "platform": "instagram", "needs_reference_photo": True},
        {"idea": "Behind the scenes look", "platform": "instagram", "needs_reference_photo": True},
        {"idea": "Customer testimonial graphic", "platform": "facebook", "needs_reference_photo": False},
        {"idea": "Quick tip / educational post", "platform": "tiktok", "needs_reference_photo": False},
    ]
    return {date: pattern[i % len(pattern)] for i, date in enumerate(dates)}


def master_planner_node(state: MarketingState):
    """
    Generates the full timeframe_days calendar in CHUNKS of
    _CALENDAR_CHUNK_SIZE days per LLM call, rather than one call for
    the whole timeframe — a single 90-entry tool call risked hitting
    Groq's tool_use_failed error even with the list-based schema, since
    the response itself gets large. Each chunk's prompt includes a
    short summary of ideas already used in earlier chunks so the
    calendar doesn't repeat itself across chunk boundaries. If one
    chunk's LLM call fails, only THAT chunk falls back to the mock
    pattern — a failure in days 41-60 doesn't discard days 1-40 that
    already generated successfully.

    Always sets plan_status='draft' — approval is a separate, explicit
    step outside this graph.
    """
    import campaign_preferences

    state["logs"].append("[Planner] Generating day-by-day content calendar...")

    campaign_id = state.get("campaign_id", "")
    prefs_text = campaign_preferences.get_preferences_text(campaign_id) if campaign_id else ""

    timeframe_days = state.get("timeframe_days", 30)
    start_date = datetime.date.today()
    all_dates = [(start_date + datetime.timedelta(days=i)).isoformat() for i in range(timeframe_days)]

    chunks = [all_dates[i:i + _CALENDAR_CHUNK_SIZE] for i in range(0, len(all_dates), _CALENDAR_CHUNK_SIZE)]

    calendar: Dict[str, Dict[str, Any]] = {}
    ideas_so_far: List[str] = []
    log_lines: List[str] = []

    from llm_client import generate_structured
    from schemas import PlannerOutput

    for chunk_index, chunk_dates in enumerate(chunks):
        continuity_note = (
            f"Ideas already used earlier in this calendar (avoid repeating these): {ideas_so_far}\n"
            if ideas_so_far else ""
        )
        user_prompt = (
            f"Business: {_full_business_context(state)}\n"
            f"Campaign goal: {state['campaign_goal']}\n"
            f"Target audience: {state['target_audience']}\n"
            f"This is chunk {chunk_index + 1} of {len(chunks)} of a {timeframe_days}-day calendar.\n"
            f"Dates to plan in THIS chunk (use these exact date strings): {chunk_dates}\n"
            f"{continuity_note}"
            f"{prefs_text}"
        )

        try:
            result: PlannerOutput = generate_structured(_PLANNER_SYSTEM_PROMPT, user_prompt, PlannerOutput)
            for entry in result.calendar_plan.days:
                if entry.date not in chunk_dates:
                    continue  # ignore any stray/hallucinated date outside this chunk
                calendar[entry.date] = {
                    "idea": entry.idea,
                    "platform": entry.platform,
                    "needs_reference_photo": entry.needs_reference_photo,
                }
                ideas_so_far.append(entry.idea)

            missing = [d for d in chunk_dates if d not in calendar]
            if missing:
                calendar.update(_fallback_pattern_for_dates(missing))
                log_lines.append(
                    f" \u26a0\ufe0f Chunk {chunk_index + 1}/{len(chunks)}: {len(missing)} date(s) missing from response, filled with fallback pattern."
                )
            else:
                log_lines.append(f" \u2705 Chunk {chunk_index + 1}/{len(chunks)}: {len(chunk_dates)} days generated.")
        except Exception as e:
            calendar.update(_fallback_pattern_for_dates(chunk_dates))
            log_lines.append(f" \u26a0\ufe0f Chunk {chunk_index + 1}/{len(chunks)} LLM call failed ({e}); used fallback pattern for these {len(chunk_dates)} days.")

    log_lines.append(f"[Planner] Finished. {len(calendar)}-day calendar built across {len(chunks)} chunk(s) (awaiting approval).")
    return {"calendar_plan": calendar, "plan_status": "draft", "logs": log_lines}


def _add_planning_nodes_and_edges(workflow: StateGraph) -> None:
    """
    This graph ONLY produces the draft calendar and stops — it does not
    generate any daily assets or publish anything. Daily generation
    happens later, per-day, via generate_daily_asset() below, triggered
    either by the daily Celery cron or on-demand through the API/chat.
    """
    workflow.add_node("context_gatherer", business_context_gatherer_node)
    workflow.add_node("business_research_node", business_research_node)
    workflow.add_node("master_planner_node", master_planner_node)

    workflow.set_entry_point("context_gatherer")
    workflow.add_edge("context_gatherer", "business_research_node")
    workflow.add_edge("business_research_node", "master_planner_node")
    workflow.add_edge("master_planner_node", END)


def build_graph():
    workflow = StateGraph(MarketingState)
    _add_planning_nodes_and_edges(workflow)
    return workflow.compile()


def build_graph_with_checkpointer(checkpointer):
    workflow = StateGraph(MarketingState)
    _add_planning_nodes_and_edges(workflow)
    return workflow.compile(checkpointer=checkpointer)


def build_replan_graph_with_checkpointer(checkpointer):
    """
    Used for plan-level 'refine': re-runs ONLY the planner node (context/
    research already happened once and don't need repeating), reading
    the newly-added preference from campaign_preferences and producing
    a fresh calendar_plan, still in 'draft' status.
    """
    workflow = StateGraph(MarketingState)
    workflow.add_node("master_planner_node", master_planner_node)
    workflow.set_entry_point("master_planner_node")
    workflow.add_edge("master_planner_node", END)
    return workflow.compile(checkpointer=checkpointer)


# ---------------------------------------------------------
# 4. DAILY ASSET GENERATION — standalone, called once per day per
#    campaign (by tasks.py's cron, or on-demand via the API/chat),
#    NOT a chained graph node. Stops at "awaiting_approval" — the ad
#    is finished and ready for the business to review. This platform
#    doesn't publish/crosspost anywhere (a separate future feature) —
#    "approved" is a terminal state meaning "ready for the business
#    to use," not "posted."
# ---------------------------------------------------------

_DAY_CONTENT_SYSTEM_PROMPT = """You are a social media copywriter and
creative director. Given a business, its target audience, and today's
planned content idea/platform, write:
- caption: the actual post caption (under 280 characters, platform-
  appropriate tone, no hashtag spam)
- image_prompt: a detailed visual description for an image generator
  to create the accompanying image

If a "Learned preferences" section is given, follow every point in it —
these are standing instructions from the business owner accumulated
over the life of this campaign.
"""


def generate_daily_asset(state: Dict[str, Any], date: str) -> Dict[str, Any]:
    """
    Generates ONE day's caption + image, for the given date, using that
    day's entry in calendar_plan. Returns a dict with the same shape as
    a partial MarketingState update — caller (CampaignManager) merges
    this into persisted state.

    If the day's idea needs a reference photo and none has been
    supplied yet (state["reference_images"].get(date)), image
    generation is skipped and asset_status is left as
    "pending_generation" rather than "awaiting_approval" — the caller
    should notify the business rather than presenting an incomplete
    draft for review.
    """
    import campaign_preferences
    from llm_client import generate_structured
    from schemas import DayContentOutput
    from image_clients import generate_image

    campaign_id = state.get("campaign_id", "")
    calendar_plan = state.get("calendar_plan", {})
    day_plan = calendar_plan.get(date)
    if day_plan is None:
        raise ValueError(f"No calendar entry for date={date!r}")

    prefs_text = campaign_preferences.get_preferences_text(campaign_id) if campaign_id else ""
    business_context = state.get("business_context", "")
    target_audience = state.get("target_audience", "")

    user_prompt = (
        f"Business: {business_context}\n"
        f"Target audience: {target_audience}\n"
        f"Today's idea: {day_plan['idea']}\n"
        f"Platform: {day_plan['platform']}\n"
        f"{prefs_text}"
    )

    try:
        content: DayContentOutput = generate_structured(_DAY_CONTENT_SYSTEM_PROMPT, user_prompt, DayContentOutput)
        caption = content.caption
        image_prompt = content.image_prompt
    except Exception:
        caption = f"{day_plan['idea']} \u2728"
        image_prompt = f"Professional social media image: {day_plan['idea']}, for {business_context}"

    reference_images = state.get("reference_images") or {}
    needs_reference = day_plan.get("needs_reference_photo", False)

    if needs_reference and date not in reference_images:
        return {
            "generated_captions": {date: caption},
            "asset_status": {date: "pending_generation"},
        }

    user_plan = state.get("user_plan") or "free"
    model = "gemini_free" if user_plan != "paid" else "dalle3"
    try:
        image_result = generate_image(prompt=image_prompt, model_preference=model)
        image_ok = True
    except Exception as e:
        image_result = {"model": model, "url": None, "error": str(e)}
        image_ok = False

    return {
        "generated_captions": {date: caption},
        "generated_images": {date: image_result},
        "asset_status": {date: "awaiting_approval" if image_ok else "pending_generation"},
    }


# ---------------------------------------------------------
# 5. MOCK EXECUTION (For testing the planning graph)
# ---------------------------------------------------------

if __name__ == "__main__":
    app = build_graph()

    initial_state = {
        "campaign_id": "demo-1",
        "business_context": "A neighborhood coffee shop known for single-origin pour-overs.",
        "campaign_goal": "Increase foot traffic and build a loyal local following.",
        "target_audience": "Young professionals and students nearby.",
        "timeframe_days": 30,
        "user_plan": "free",
        "business_website_url": None,
        "facebook_page_url": None,
        "reference_images": {},
        "logs": [],
    }

    print("--- STARTING PLANNING WORKFLOW ---")
    for output in app.stream(initial_state):
        for key, value in output.items():
            print(f"Node: {key}")
            if "logs" in value:
                print(value["logs"][-1])
            print("---")

    print("\nPlanning complete! Calendar is in 'draft' status, awaiting owner approval.")
