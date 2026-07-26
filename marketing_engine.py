from typing import TypedDict, List, Dict, Any, Optional
import operator
from typing import Annotated
from langgraph.graph import StateGraph, END

# ---------------------------------------------------------
# 1. STATE DEFINITION
# ---------------------------------------------------------
class MarketingState(TypedDict):
    business_context: str
    campaign_goal: str
    target_audience: str
    total_budget: float
    
    # Planners will populate this
    campaign_plan: Optional[Dict[str, Any]]
    budget_allocations: Optional[Dict[str, float]]
    
    # Generation outputs
    generated_copy: Optional[Dict[str, str]]
    generated_images: Optional[Dict[str, Dict[str, str]]] # e.g. {"ig_post_1": {"model": "dalle3", "url": "..."}}
    generated_videos: Optional[Dict[str, Dict[str, str]]]
    
    # Final statuses
    publishing_status: Optional[Dict[str, str]]
    
    # System logs
    logs: Annotated[List[str], operator.add]

# ---------------------------------------------------------
# 2. PLANNING NODES
# ---------------------------------------------------------
def replanning_evaluator_node(state: MarketingState):
    """Evaluates if the current context requires a new plan."""
    state['logs'].append("[Evaluator] Checking business context...")
    # Mock logic: if no plan exists, we need one. 
    needs_plan = True if not state.get('campaign_plan') else False
    
    return {"logs": [f"[Evaluator] Needs new plan: {needs_plan}"]}

def master_planner_node(state: MarketingState):
    """Generates the multi-week campaign strategy and budget distribution."""
    state['logs'].append("[Planner] Generating comprehensive campaign strategy...")
    
    # Mock generation of a 4-week plan
    mock_plan = {
        "week_1": ["Brand Awareness Video", "Product Showcase Image"],
        "week_2": ["Customer Testimonial Video", "Lifestyle Image"],
        "week_3": ["UGC Style Avatar Video", "Promo Image"],
        "week_4": ["Retargeting Video", "Urgency Image"]
    }
    
    mock_budgets = {
        "facebook_ads": state['total_budget'] * 0.5,
        "tiktok_ads": state['total_budget'] * 0.3,
        "google_ads": state['total_budget'] * 0.2
    }
    
    return {
        "campaign_plan": mock_plan,
        "budget_allocations": mock_budgets,
        "logs": ["[Planner] 4-week plan and budgets established."]
    }

def content_ideation_node(state: MarketingState):
    """Generates the text copy and prompts for the media assets."""
    state['logs'].append("[Ideation] Generating ad copy and media prompts...")
    # Mock copy generation
    mock_copy = {
        "ig_post_1": "Discover our new collection! 🚀",
        "tiktok_vid_1": "Watch how this changes everything. #wow"
    }
    return {"generated_copy": mock_copy, "logs": ["[Ideation] Copy generated."]}

# ---------------------------------------------------------
# 3. GENERATION ROUTING NODES
# ---------------------------------------------------------

# Keywords that signal photorealistic / product imagery → Stable Diffusion
_SD_KEYWORDS = {"product", "showcase", "lifestyle", "photorealistic", "fashion", "photo", "model"}
# Everything else defaults to DALL-E 3 (creative, brand, abstract, promo)

def _pick_image_model(asset_description: str) -> str:
    """Heuristic: pick the best image model based on the content type."""
    lower = asset_description.lower()
    if any(kw in lower for kw in _SD_KEYWORDS):
        return "stable_diffusion"
    return "dalle3"

def _build_image_prompt(asset_description: str, state: MarketingState) -> str:
    """Build a rich prompt by combining the asset description with campaign context."""
    return (
        f"Create a professional marketing image for the following campaign.\n"
        f"Business: {state['business_context']}\n"
        f"Target Audience: {state['target_audience']}\n"
        f"Asset Type: {asset_description}\n"
        f"Style: High-end, modern, scroll-stopping social media ad.\n"
        f"Do NOT include any text or watermarks in the image."
    )

def image_generation_router(state: MarketingState):
    """
    Intelligent image router that inspects the campaign plan,
    picks DALL-E 3 or Stable Diffusion per asset, and generates images.
    
    Falls back to mock results if API keys are not set (dry-run mode).
    """
    from image_clients import generate_image  # local import to avoid circular deps

    plan = state.get("campaign_plan", {})
    images: Dict[str, Dict[str, str]] = {}
    log_lines: List[str] = ["[Image Router] Routing image requests..."]

    # Iterate over the plan and generate images for every "Image" asset
    for week_key, assets in plan.items():
        for asset_desc in assets:
            if "image" not in asset_desc.lower():
                continue  # skip non-image assets (videos handled elsewhere)

            asset_id = f"{week_key}_{asset_desc.replace(' ', '_').lower()}"
            model = _pick_image_model(asset_desc)
            prompt = _build_image_prompt(asset_desc, state)

            try:
                result = generate_image(prompt=prompt, model_preference=model)
                images[asset_id] = result
                log_lines.append(
                    f"  ✅ {asset_id} → {model} | "
                    f"path={result.get('local_path', result.get('url', 'n/a'))}"
                )
            except Exception as e:
                # Graceful fallback so the graph doesn't crash
                images[asset_id] = {"model": model, "url": None, "error": str(e)}
                log_lines.append(f"  ⚠️ {asset_id} → {model} FAILED: {e}")

    log_lines.append(f"[Image Router] Finished. {len(images)} image(s) processed.")
    return {"generated_images": images, "logs": log_lines}


def _build_video_script(asset_description: str, state: MarketingState, is_avatar: bool) -> str:
    """Build a script for avatar videos or a prompt for text-to-video models."""
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
            f"Business: {state['business_context']}\n"
            f"Target Audience: {state['target_audience']}\n"
            f"Asset Type: {asset_description}\n"
            f"Style: Premium, modern, scroll-stopping. No text overlays."
        )

def video_generation_router(state: MarketingState):
    """
    Intelligent video router that inspects the campaign plan,
    picks HeyGen/Synthesia/Runway/Luma per asset, and generates videos.
    
    Falls back gracefully if API keys are not set.
    """
    from video_clients import generate_video, pick_video_model

    plan = state.get("campaign_plan", {})
    videos: Dict[str, Dict[str, str]] = {}
    log_lines: List[str] = ["[Video Router] Routing video requests..."]

    # Iterate over the plan and generate videos for every "Video" asset
    for week_key, assets in plan.items():
        for asset_desc in assets:
            if "video" not in asset_desc.lower():
                continue  # skip non-video assets (images handled elsewhere)

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
                    f"  [OK] {asset_id} -> {model} | "
                    f"id={result.get('video_id', result.get('task_id', result.get('generation_id', 'n/a')))}"
                )
            except Exception as e:
                # Graceful fallback so the graph doesn't crash
                videos[asset_id] = {"model": model, "video_url": None, "error": str(e)}
                log_lines.append(f"  [WARN] {asset_id} -> {model} FAILED: {e}")

    log_lines.append(f"[Video Router] Finished. {len(videos)} video(s) processed.")
    return {"generated_videos": videos, "logs": log_lines}

# ---------------------------------------------------------
# 4. EXECUTION NODES
# ---------------------------------------------------------
def ad_platform_publisher(state: MarketingState):
    """
    Pushes the generated creatives and allocated budgets directly 
    to Facebook Ads, TikTok Ads, and Google Ads APIs.
    """
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
        log_lines.append(f"  ✅ [{platform.upper()}] Status: {st} | Budget: ${b:.2f}")

    log_lines.append("[Publisher] Campaigns and budgets deployed.")
    return {"publishing_status": status, "logs": log_lines}

# ---------------------------------------------------------
# 5. GRAPH DEFINITION & ROUTING LOGIC
# ---------------------------------------------------------
def should_plan(state: MarketingState):
    """Conditional edge logic: route to planner if needed, else skip to ideation."""
    if not state.get('campaign_plan'):
        return "master_planner_node"
    return "content_ideation_node"

def build_graph():
    workflow = StateGraph(MarketingState)

    # Add Nodes
    workflow.add_node("evaluator", replanning_evaluator_node)
    workflow.add_node("master_planner_node", master_planner_node)
    workflow.add_node("content_ideation_node", content_ideation_node)
    workflow.add_node("image_generation_router", image_generation_router)
    workflow.add_node("video_generation_router", video_generation_router)
    workflow.add_node("ad_platform_publisher", ad_platform_publisher)

    # Add Edges
    workflow.set_entry_point("evaluator")
    
    # Conditional logic after evaluator
    workflow.add_conditional_edges(
        "evaluator",
        should_plan,
        {
            "master_planner_node": "master_planner_node",
            "content_ideation_node": "content_ideation_node"
        }
    )
    
    # Flow from planner to ideation
    workflow.add_edge("master_planner_node", "content_ideation_node")
    
    # After ideation, we can generate images and videos in parallel
    workflow.add_edge("content_ideation_node", "image_generation_router")
    workflow.add_edge("content_ideation_node", "video_generation_router")
    
    # Both image and video gen need to finish before publishing
    workflow.add_edge("image_generation_router", "ad_platform_publisher")
    workflow.add_edge("video_generation_router", "ad_platform_publisher")
    
    workflow.add_edge("ad_platform_publisher", END)

    return workflow.compile()

# ---------------------------------------------------------
# 6. MOCK EXECUTION (For testing)
# ---------------------------------------------------------
if __name__ == "__main__":
    app = build_graph()
    
    initial_state = {
        "business_context": "We are a SaaS startup launching a new AI tool. We need aggressive growth.",
        "campaign_goal": "Acquire 1000 new signups this month.",
        "target_audience": "Tech founders, marketers, and developers.",
        "total_budget": 5000.0,
        "logs": []
    }
    
    print("--- STARTING WORKFLOW ---")
    for output in app.stream(initial_state):
        for key, value in output.items():
            print(f"Node: {key}")
            if "logs" in value:
                print(value["logs"][-1])
            print("---")
    
    print("\\nWorkflow Complete!")
