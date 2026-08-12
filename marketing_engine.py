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

    user_plan: Optional[str]  # "free" or "paid" — reserved for future plan-gated features

    # How many days ahead the daily cron should try to keep generated
    # and waiting for review, beyond just today. Default 1 (today only).
    # Business can raise this (e.g. 3) to always have a few days' worth
    # of drafts ready to review at once, without waiting one-per-day.
    # Independent of this, generate_days_ahead() lets a business trigger
    # a batch on demand at any time — this setting only affects what the
    # unattended cron does by itself.
    auto_generate_buffer_days: Optional[int]

    business_website_url: Optional[str]
    facebook_page_url: Optional[str]
    website_context: Optional[str]
    facebook_context: Optional[str]
    research_notes: Optional[str]

    # Plan-level gate
    # Plan-level gate — a LIGHTWEIGHT strategy outline (content
    # pillars, tone, platform mix), not a full day-by-day calendar.
    # One small LLM call regardless of timeframe_days.
    strategy_outline: Optional[Dict[str, Any]]  # {content_pillars, tone, platform_mix, notes}
    calendar_dates: Optional[List[str]]  # plain list of ISO dates for the timeframe, computed in Python (no LLM)
    plan_status: Optional[str]  # "draft" | "approved"

    # Populated INCREMENTALLY, one day at a time, only as each day is
    # actually generated (not upfront) — date -> {idea, platform, needs_reference_photo}
    calendar_plan: Optional[Dict[str, Dict[str, Any]]]

    # Per-day asset state (populated incrementally, one day at a time —
    # NOT all generated up front)
    generated_captions: Optional[Dict[str, str]]      # date -> caption
    ad_copy_variants: Optional[Dict[str, List[str]]]  # date -> [variant, variant, ...]
    image_prompts: Optional[Dict[str, str]]           # date -> text-to-image prompt (for the business to use in a tool of their choice)
    asset_status: Optional[Dict[str, str]]            # date -> "awaiting_approval" | "approved" | "published"

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
# 3. PLANNER — produces a LIGHTWEIGHT strategy outline (content
#    pillars, tone, platform mix), NOT a day-by-day calendar. This is
#    one small LLM call regardless of whether timeframe_days is 30 or
#    90 — the per-day specifics get decided later, one day at a time,
#    only when that day is actually about to be generated (see
#    generate_daily_asset below). This is what keeps token usage low:
#    no more paying to plan 90 days of content upfront before a single
#    post has even been reviewed.
# ---------------------------------------------------------

_PLANNER_SYSTEM_PROMPT = """You are a senior organic social media strategist.
Given a business, a campaign goal, a target audience, and a 3-day timeframe (3d content calendar),
produce a SHORT strategic outline — not a long calendar, just the
recurring structure that will guide day-by-day content decisions for this 3-day scope.

Respond ONLY with the structured output requested:
- content_pillars: 3-6 recurring themes to rotate through (e.g. product
  highlights, behind the scenes, testimonials, educational tips,
  promotions, community engagement) — specific to this business, not generic
- tone: the brand voice captions should use
- platform_mix: which platforms to prioritize and roughly how often
- notes: any other short strategic guidance (optional)

Ground this in the business's real context and any research notes
provided. If a "Learned preferences" section is given, follow it.
"""


def master_planner_node(state: MarketingState):
    """
    Generates the lightweight 3-day strategy outline in ONE LLM call.
    Always sets plan_status='draft' — approval is a separate, explicit step
    outside this graph. calendar_dates (the plain list of ISO dates for
    the 3-day timeframe) is computed here in Python, no LLM call needed for
    that part — it's just today + timeframe_days (default 3).
    """
    import campaign_preferences

    state["logs"].append("[Planner] Generating lightweight 3-day strategy outline...")

    campaign_id = state.get("campaign_id", "")
    prefs_text = campaign_preferences.get_preferences_text(campaign_id) if campaign_id else ""

    timeframe_days = state.get("timeframe_days", 3)
    start_date = datetime.date.today()
    calendar_dates = [(start_date + datetime.timedelta(days=i)).isoformat() for i in range(timeframe_days)]

    user_prompt = (
        f"Business: {_full_business_context(state)}\n"
        f"Campaign goal: {state['campaign_goal']}\n"
        f"Target audience: {state['target_audience']}\n"
        f"Timeframe: {timeframe_days} days\n"
        f"{prefs_text}"
    )

    try:
        from llm_client import generate_structured
        from schemas import PlannerOutput

        result: PlannerOutput = generate_structured(_PLANNER_SYSTEM_PROMPT, user_prompt, PlannerOutput)
        outline = result.strategy_outline.model_dump()
        return {
            "strategy_outline": outline,
            "calendar_dates": calendar_dates,
            "calendar_plan": {},  # populated incrementally, one day at a time, as each day is actually generated
            "plan_status": "draft",
            "logs": [f"[Planner] 3-day strategy outline generated ({len(outline.get('content_pillars', []))} content pillars), awaiting approval."],
        }
    except Exception as e:
        fallback_outline = {
            "content_pillars": ["Product highlight", "Behind the scenes", "Customer testimonial", "Educational tip", "Promotion"],
            "tone": "friendly and approachable",
            "platform_mix": "Instagram most days, Facebook a couple times a week",
            "notes": None,
        }
        return {
            "strategy_outline": fallback_outline,
            "calendar_dates": calendar_dates,
            "calendar_plan": {},
            "plan_status": "draft",
            "logs": [f"[Planner] LLM call failed ({e}); used fallback outline."],
        }


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
    a fresh strategy_outline, still in 'draft' status.
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

_DAY_CONTENT_SYSTEM_PROMPT = """You are a social media strategist and
copywriter. Given a business's strategy outline (content pillars, tone,
platform mix) and where today falls in the campaign, decide TODAY's
specific content idea and write it up.

Respond ONLY with the structured output requested:
- idea: today's specific content idea, drawn from the strategy's
  content pillars — rotate through them, don't repeat a recently-used idea
- platform: which single platform today's post targets, consistent
  with the platform mix
- needs_reference_photo: true if this idea would land better with a
  real business-supplied photo (product, founder, team, store) than a
  generic/stock visual — informational, just flags it to the business
- caption: the primary post caption (under 280 characters, matching
  the strategy's tone, no hashtag spam)
- ad_copy_variants: 2-3 alternative short ad copy variants on the same
  idea (different hooks/angles), for the business to A/B test or pick from
- image_prompt: a detailed, ready-to-use text-to-image prompt (subject,
  style, composition, lighting, mood) the business can paste into an
  image generation tool of their choice

If a "Learned preferences" section is given, follow every point in it —
these are standing instructions from the business owner accumulated
over the life of this campaign.
"""


def generate_daily_asset(state: Dict[str, Any], date: str) -> Dict[str, Any]:
    """
    Generates ONE day's idea + platform + reference-need + caption +
    ad copy variants + image prompt, all in a SINGLE combined LLM call,
    using the campaign's lightweight strategy_outline (not a
    precomputed per-day plan) plus recently-used ideas for variety.
    This is what keeps token usage low: the outline itself is cheap
    and generated once regardless of timeframe length, and each day's
    specifics only get decided (and paid for) at the moment that day
    is actually about to be reviewed — never speculatively for days
    far in the future.

    Returns a dict with the same shape as a partial MarketingState
    update — caller (CampaignManager) merges this into persisted state,
    including writing this day's derived {idea, platform,
    needs_reference_photo} into calendar_plan[date] for record-keeping.

    No image is generated here — the platform produces the caption, ad
    copy variants, and a ready-to-use image_prompt (text-to-image
    prompt) that the business can paste into an image generation tool
    of their own choice. needs_reference_photo is purely informational:
    it tells the business this idea would land better with their own
    real photo than a generic/stock visual.
    """
    import campaign_preferences
    from llm_client import generate_structured
    from schemas import DayContentOutput

    campaign_id = state.get("campaign_id", "")
    calendar_dates = state.get("calendar_dates") or []
    if date not in calendar_dates:
        raise ValueError(f"date={date!r} is not within this campaign's planned timeframe")

    strategy_outline = state.get("strategy_outline") or {}
    calendar_plan = state.get("calendar_plan") or {}
    prefs_text = campaign_preferences.get_preferences_text(campaign_id) if campaign_id else ""
    business_context = state.get("business_context", "")
    target_audience = state.get("target_audience", "")

    day_index = calendar_dates.index(date) + 1
    recent_ideas = [
        calendar_plan[d]["idea"] for d in sorted(calendar_plan.keys()) if d < date and d in calendar_plan
    ][-10:]

    user_prompt = (
        f"Business: {business_context}\n"
        f"Target audience: {target_audience}\n"
        f"Strategy outline: {strategy_outline}\n"
        f"Today is day {day_index} of {len(calendar_dates)} ({date}).\n"
        f"Recently used ideas (avoid repeating these): {recent_ideas}\n"
        f"{prefs_text}"
    )

    try:
        content: DayContentOutput = generate_structured(_DAY_CONTENT_SYSTEM_PROMPT, user_prompt, DayContentOutput)
        idea, platform, needs_reference = content.idea, content.platform, content.needs_reference_photo
        caption, ad_copy_variants, image_prompt = content.caption, content.ad_copy_variants, content.image_prompt
    except Exception:
        pillars = strategy_outline.get("content_pillars") or ["Product highlight"]
        idea = pillars[(day_index - 1) % len(pillars)]
        platform = "instagram"
        needs_reference = False
        caption = f"{idea} \u2728"
        ad_copy_variants = [caption]
        image_prompt = f"Professional social media image: {idea}, for {business_context}"

    if needs_reference:
        # Purely informational — no generation is gated on this. Just
        # makes sure the note travels with the prompt so the business
        # sees it wherever image_prompt is surfaced.
        image_prompt = (
            f"{image_prompt}\n\n"
            f"Note: this idea will land best with your own real photo "
            f"(product, founder, team, or venue) rather than a purely "
            f"AI-generated image — use this prompt as a starting point "
            f"only if you don't have one to use."
        )

    return {
        "calendar_plan": {date: {"idea": idea, "platform": platform, "needs_reference_photo": needs_reference}},
        "generated_captions": {date: caption},
        "ad_copy_variants": {date: ad_copy_variants},
        "image_prompts": {date: image_prompt},
        "asset_status": {date: "awaiting_approval"},
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
