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

    # Optional enrichment sources — never required to run the pipeline
    business_website_url: Optional[str]
    facebook_page_url: Optional[str]
    website_context: Optional[str]
    facebook_context: Optional[str]

    # Set to True to force the planner to run again on an already-planned
    # campaign (e.g. business added a new product or a new campaign brief
    # while a previous plan for this campaign_id was already executing)
    force_replan: Optional[bool]

    # Planners will populate this
    campaign_plan: Optional[Dict[str, Any]]
    budget_allocations: Optional[Dict[str, float]]

    # Generation outputs
    generated_copy: Optional[Dict[str, str]]
    generated_images: Optional[Dict[str, Dict[str, str]]]  # e.g. {"ig_post_1": {"model": "dalle3", "url": "..."}}
    generated_videos: Optional[Dict[str, Dict[str, str]]]

    # Final statuses
    publishing_status: Optional[Dict[str, str]]

    # System logs
    logs: Annotated[List[str], operator.add]


# ---------------------------------------------------------
# 2. CONTEXT GATHERING NODE (optional, non-blocking)
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
            f" ✅ Website context pulled from {website_url}" if website_context
            else f" ⚠️ Could not pull website context from {website_url} (continuing without it)"
        )
    else:
        log_lines.append(" - No website URL provided, skipping.")

    facebook_url = state.get("facebook_page_url")
    if facebook_url:
        facebook_context = fetch_facebook_context(facebook_url)
        log_lines.append(
            f" ✅ Facebook Page context pulled from {facebook_url}" if facebook_context
            else f" ⚠️ Could not pull Facebook context from {facebook_url} (continuing without it)"
        )
    else:
        log_lines.append(" - No Facebook Page URL provided, skipping.")

    return {
        "website_context": website_context,
        "facebook_context": facebook_context,
        "logs": log_lines,
    }


def _full_business_context(state: MarketingState) -> str:
    """Combines the raw business_context with any enrichment we managed to pull."""
    parts = [state.get("business_context", "")]
    if state.get("website_context"):
        parts.append(f"[From business website]\n{state['website_context']}")
    if state.get("facebook_context"):
        parts.append(f"[From Facebook Page]\n{state['facebook_context']}")
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
- Each asset description must clearly indicate whether it is an "Image" or "Video"
  asset (e.g. "Product Showcase Image", "Customer Testimonial Video") so downstream
  routers can detect the asset type from the string.
- budget_allocations values must sum to the total_budget provided.
- Tailor asset ideas to the business context, goal, and audience — avoid generic filler.
- If website or Facebook context is included, use it to ground the plan in the
  business's actual products, tone, and positioning rather than guessing.
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
            "week_1": ["Brand Awareness Video", "Product Showcase Image"],
            "week_2": ["Customer Testimonial Video", "Lifestyle Image"],
            "week_3": ["UGC Style Avatar Video", "Promo Image"],
            "week_4": ["Retargeting Video", "Urgency Image"],
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
            "ig_post_1": "Discover our new collection! 🚀",
            "tiktok_vid_1": "Watch how this changes everything. #wow",
        }
        return {
            "generated_copy": mock_copy,
            "logs": [f"[Ideation] LLM call failed ({e}); used fallback mock copy."],
        }


# ---------------------------------------------------------
# 4. GENERATION ROUTING NODES
# ---------------------------------------------------------

_SD_KEYWORDS = {"product", "showcase", "lifestyle", "photorealistic", "fashion", "photo", "model"}


def _pick_image_model(asset_description: str) -> str:
    lower = asset_description.lower()
    if any(kw in lower for kw in _SD_KEYWORDS):
        return "stable_diffusion"
    return "dalle3"


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
    from image_clients import generate_image

    plan = state.get("campaign_plan", {})
    images: Dict[str, Dict[str, str]] = {}
    log_lines: List[str] = ["[Image Router] Routing image requests..."]

    for week_key, assets in plan.items():
        for asset_desc in assets:
            if "image" not in asset_desc.lower():
                continue
            asset_id = f"{week_key}_{asset_desc.replace(' ', '_').lower()}"
            model = _pick_image_model(asset_desc)
            prompt = _build_image_prompt(asset_desc, state)
            try:
                result = generate_image(prompt=prompt, model_preference=model)
                images[asset_id] = result
                log_lines.append(
                    f" ✅ {asset_id} → {model} | "
                    f"path={result.get('local_path', result.get('url', 'n/a'))}"
                )
            except Exception as e:
                images[asset_id] = {"model": model, "url": None, "error": str(e)}
                log_lines.append(f" ⚠️ {asset_id} → {model} FAILED: {e}")

    log_lines.append(f"[Image Router] Finished. {len(images)} image(s) processed.")
    return {"generated_images": images, "logs": log_lines}


def _build_video_script(asset_description: str, state: MarketingState, is_avatar: bool) -> str:
    if is_avatar:
        return (
            f"Hi there! I'm excited to share something amazing with you. "
            f"{state.get('campaign_goal', '')} "
            f"Our product is built for {state.get('target_audience', 'you')}. "
            f"Don't miss out — check the link in our bio!"
        )
    else:
        return (
            f"Create a cinematic, high-quality marketing video clip.\n"
            f"Business: {_full_business_context(state)}\n"
            f"Target Audience: {state['target_audience']}\n"
            f"Asset Type: {asset_description}\n"
            f"Style: Premium, modern, scroll-stopping. No text overlays."
        )


def video_generation_router(state: MarketingState):
    from video_clients import generate_video, pick_video_model

    plan = state.get("campaign_plan", {})
    videos: Dict[str, Dict[str, str]] = {}
    log_lines: List[str] = ["[Video Router] Routing video requests..."]

    for week_key, assets in plan.items():
        for asset_desc in assets:
            if "video" not in asset_desc.lower():
                continue
            asset_id = f"{week_key}_{asset_desc.replace(' ', '_').lower()}"
            model = pick_video_model(asset_desc)
            is_avatar = model in ("heygen", "synthesia")
            script_or_prompt = _build_video_script(asset_desc, state, is_avatar)
            try:
                result = generate_video(
                    script_or_prompt=script_or_prompt,
                    model_preference=model,
                )
                videos[asset_id] = result
                log_lines.append(
                    f" [OK] {asset_id} -> {model} | "
                    f"id={result.get('video_id', result.get('task_id', result.get('generation_id', 'n/a')))}"
                )
            except Exception as e:
                videos[asset_id] = {"model": model, "video_url": None, "error": str(e)}
                log_lines.append(f" [WARN] {asset_id} -> {model} FAILED: {e}")

    log_lines.append(f"[Video Router] Finished. {len(videos)} video(s) processed.")
    return {"generated_videos": videos, "logs": log_lines}


# ---------------------------------------------------------
# 5. EXECUTION NODES
# ---------------------------------------------------------

def ad_platform_publisher(state: MarketingState):
    from publisher_clients import publish_to_all_platforms

    log_lines = ["[Publisher] Pushing campaigns to Facebook, TikTok, Google..."]
    campaign_name = f"Campaign - {state.get('campaign_goal', 'Growth')}"
    budgets = state.get("budget_allocations", {})
    images = state.get("generated_images", {})
    videos = state.get("generated_videos", {})

    status = publish_to_all_platforms(
        campaign_name=campaign_name,
        budget_allocations=budgets,
        images=images,
        videos=videos,
    )

    for platform, info in status.items():
        st = info.get("status", "PAUSED")
        b = info.get("budget_allocated_usd", 0.0)
        log_lines.append(f" ✅ [{platform.upper()}] Status: {st} | Budget: ${b:.2f}")

    log_lines.append("[Publisher] Campaigns and budgets deployed.")
    return {"publishing_status": status, "logs": log_lines}


# ---------------------------------------------------------
# 6. GRAPH DEFINITION & ROUTING LOGIC
# ---------------------------------------------------------

def should_plan(state: MarketingState):
    """Conditional edge logic: route to planner if needed, else skip to ideation."""
    if not state.get('campaign_plan') or state.get('force_replan'):
        return "master_planner_node"
    return "content_ideation_node"


def build_graph():
    """
    Note: build_graph() alone gives you a single-run graph, same as before.
    For production use where multiple campaigns for the same business run
    concurrently, and a running campaign can be rerouted mid-flight when a
    new product/campaign brief comes in, compile with a checkpointer and
    a per-campaign thread_id — see campaign_manager.py.
    """
    workflow = StateGraph(MarketingState)

    workflow.add_node("context_gatherer", business_context_gatherer_node)
    workflow.add_node("evaluator", replanning_evaluator_node)
    workflow.add_node("master_planner_node", master_planner_node)
    workflow.add_node("content_ideation_node", content_ideation_node)
    workflow.add_node("image_generation_router", image_generation_router)
    workflow.add_node("video_generation_router", video_generation_router)
    workflow.add_node("ad_platform_publisher", ad_platform_publisher)

    workflow.set_entry_point("context_gatherer")
    workflow.add_edge("context_gatherer", "evaluator")

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
    workflow.add_edge("content_ideation_node", "video_generation_router")
    workflow.add_edge("image_generation_router", "ad_platform_publisher")
    workflow.add_edge("video_generation_router", "ad_platform_publisher")
    workflow.add_edge("ad_platform_publisher", END)

    return workflow.compile()


def build_graph_with_checkpointer(checkpointer):
    """
    Same graph as build_graph(), but compiled with a checkpointer so state
    is persisted per thread_id (= campaign_id). This is what makes it safe
    to run many campaigns concurrently and to reroute a running one — see
    campaign_manager.py.
    """
    workflow = StateGraph(MarketingState)

    workflow.add_node("context_gatherer", business_context_gatherer_node)
    workflow.add_node("evaluator", replanning_evaluator_node)
    workflow.add_node("master_planner_node", master_planner_node)
    workflow.add_node("content_ideation_node", content_ideation_node)
    workflow.add_node("image_generation_router", image_generation_router)
    workflow.add_node("video_generation_router", video_generation_router)
    workflow.add_node("ad_platform_publisher", ad_platform_publisher)

    workflow.set_entry_point("context_gatherer")
    workflow.add_edge("context_gatherer", "evaluator")

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
    workflow.add_edge("content_ideation_node", "video_generation_router")
    workflow.add_edge("image_generation_router", "ad_platform_publisher")
    workflow.add_edge("video_generation_router", "ad_platform_publisher")
    workflow.add_edge("ad_platform_publisher", END)

    return workflow.compile(checkpointer=checkpointer)


# ---------------------------------------------------------
# 7. MOCK EXECUTION (For testing)
# ---------------------------------------------------------

if __name__ == "__main__":
    app = build_graph()

    initial_state = {
        "campaign_id": "demo-1",
        "business_context": "We are a SaaS startup launching a new AI tool. We need aggressive growth.",
        "campaign_goal": "Acquire 1000 new signups this month.",
        "target_audience": "Tech founders, marketers, and developers.",
        "total_budget": 5000.0,
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
