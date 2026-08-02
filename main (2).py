# ---------------------------------------------------------
# FastAPI Web Application & API Server for Marketing Engine
# Designed for deployment on Railway (web + worker + beat services)
# Connects to Lovable Frontend via REST API with CORS
# ---------------------------------------------------------
import os
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from marketing_engine import build_graph
from campaign_manager import CampaignManager
from config import ASSETS_DIR
import chat_router
import campaign_events

app = FastAPI(
    title="Autonomous Marketing Engine API",
    description=(
        "Backend API powering AI business research (Brave Search + Firecrawl), "
        "campaign planning, calendar scheduling, image generation, and ad "
        "platform publishing."
    ),
    version="2.0.0",
)

# ---------------------------------------------------------
# CORS Configuration for Lovable Frontend
# ---------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows Lovable frontend and localhost to make requests
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve generated media assets statically
app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")

# Compile the single-run graph once on startup (used by /api/campaign/run,
# a simple synchronous trigger). Multi-campaign, reroutable, cron-published
# campaigns go through CampaignManager instead — see below.
graph_app = build_graph()

_campaign_manager: Optional[CampaignManager] = None


def get_manager() -> CampaignManager:
    global _campaign_manager
    if _campaign_manager is None:
        _campaign_manager = CampaignManager()
    return _campaign_manager


# ---------------------------------------------------------
# Request & Response Schemas
# ---------------------------------------------------------
class CampaignRequest(BaseModel):
    business_context: str = Field(
        ...,
        example="We are a luxury fashion brand launching our summer cardigan collection.",
        description="Description of the business, brand voice, and product."
    )
    campaign_goal: str = Field(
        ...,
        example="Acquire 1000 new customers and drive $50k in sales.",
        description="Primary objective of the campaign."
    )
    target_audience: str = Field(
        ...,
        example="Women aged 20-40, fashion enthusiasts, urban professionals.",
        description="Target demographic."
    )
    total_budget: float = Field(
        5000.0,
        gt=0,
        example=5000.0,
        description="Total campaign budget in USD."
    )
    user_plan: str = Field(
        "free",
        example="free",
        description="'free' (Gemini free-tier images only) or 'paid' (DALL-E 3 / Stable Diffusion).",
    )
    business_website_url: Optional[str] = None
    facebook_page_url: Optional[str] = None
    calendar_id: Optional[str] = Field(
        None, description="Calendar id to schedule daily publish events against, if using a calendar MCP."
    )


class CampaignResponse(BaseModel):
    status: str
    campaign_plan: Optional[Dict[str, Any]]
    budget_allocations: Optional[Dict[str, float]]
    generated_copy: Optional[Dict[str, str]]
    generated_images: Optional[Dict[str, Any]]
    pending_reference_requests: Optional[List[str]]
    publish_schedule: Optional[Dict[str, str]]
    publishing_status: Optional[Dict[str, Any]]
    logs: List[str]


class StartCampaignRequest(CampaignRequest):
    campaign_id: str = Field(..., example="acme-summer-launch")


class RerouteCampaignRequest(BaseModel):
    business_context: Optional[str] = None
    campaign_goal: Optional[str] = None
    target_audience: Optional[str] = None
    total_budget: Optional[float] = None
    business_website_url: Optional[str] = None
    facebook_page_url: Optional[str] = None


class ChatMessage(BaseModel):
    role: str = Field(..., example="user")
    content: str


class ChatRequest(BaseModel):
    messages: List[ChatMessage] = Field(
        ...,
        description="Full conversation history, oldest first.",
    )


# ---------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------
@app.get("/")
def root():
    """Health check endpoint for Railway."""
    return {
        "status": "online",
        "service": "Autonomous Marketing Engine API",
        "version": "2.0.0",
        "docs_url": "/docs",
    }


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/api/campaign/run", response_model=CampaignResponse)
def run_campaign(request: CampaignRequest):
    """
    Single synchronous run of the complete pipeline (no persistence, no
    cron follow-up). Good for a quick demo/test:
      1. Business research (Brave Search + Firecrawl) + context enrichment
      2. Strategic planning & budget allocation
      3. Ad copy ideation
      4. Image generation (Gemini free tier, or DALL-E 3 / Stable
         Diffusion on the paid tier) — assets needing a real reference
         photo are skipped and listed in pending_reference_requests
      5. Calendar scheduling (one asset per day)
      6. Initial publish to Facebook, TikTok, and Google Ads APIs

    For campaigns that should persist, be rerouted, and actually publish
    on their scheduled days via the daily Celery task, use
    /api/campaign/start instead.
    """
    initial_state = {
        "business_context": request.business_context,
        "campaign_goal": request.campaign_goal,
        "target_audience": request.target_audience,
        "total_budget": request.total_budget,
        "user_plan": request.user_plan,
        "business_website_url": request.business_website_url,
        "facebook_page_url": request.facebook_page_url,
        "calendar_id": request.calendar_id,
        "reference_images": {},
        "logs": [],
    }

    try:
        final_state = {}
        for output in graph_app.stream(initial_state):
            for node_name, state_chunk in output.items():
                final_state.update(state_chunk)

        cumulative_state = graph_app.get_state({"configurable": {"thread_id": "default"}}).values

        return CampaignResponse(
            status="completed",
            campaign_plan=final_state.get("campaign_plan") or cumulative_state.get("campaign_plan"),
            budget_allocations=final_state.get("budget_allocations") or cumulative_state.get("budget_allocations"),
            generated_copy=final_state.get("generated_copy") or cumulative_state.get("generated_copy"),
            generated_images=final_state.get("generated_images") or cumulative_state.get("generated_images"),
            pending_reference_requests=final_state.get("pending_reference_requests") or cumulative_state.get("pending_reference_requests"),
            publish_schedule=final_state.get("publish_schedule") or cumulative_state.get("publish_schedule"),
            publishing_status=final_state.get("publishing_status") or cumulative_state.get("publishing_status"),
            logs=final_state.get("logs", []),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Campaign execution failed: {str(e)}")


@app.post("/api/campaign/start")
def start_campaign(request: StartCampaignRequest):
    """
    Kicks off a persistent, cron-published campaign in the background
    via CampaignManager. Poll /api/campaign/{campaign_id}/status for
    progress. Once planned + scheduled, tasks.py's daily Celery task
    publishes newly-due assets automatically each day.
    """
    manager = get_manager()
    manager.start_campaign(request.campaign_id, request.model_dump())
    return {"status": "started", "campaign_id": request.campaign_id}


@app.post("/api/campaign/{campaign_id}/reroute")
def reroute_campaign(campaign_id: str, request: RerouteCampaignRequest):
    """Updates a running/completed campaign's brief and forces a replan."""
    manager = get_manager()
    updates = {k: v for k, v in request.model_dump().items() if v is not None}
    try:
        manager.reroute_campaign(campaign_id, updates)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"status": "rerouting", "campaign_id": campaign_id}


@app.get("/api/campaign/{campaign_id}/status")
def campaign_status(campaign_id: str):
    manager = get_manager()
    state = manager.get_status(campaign_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"No campaign found for campaign_id={campaign_id!r}")
    return {
        "campaign_id": campaign_id,
        "is_running": manager.is_running(campaign_id),
        "campaign_plan": state.get("campaign_plan"),
        "budget_allocations": state.get("budget_allocations"),
        "generated_copy": state.get("generated_copy"),
        "generated_images": state.get("generated_images"),
        "pending_reference_requests": state.get("pending_reference_requests"),
        "publish_schedule": state.get("publish_schedule"),
        "publishing_status": state.get("publishing_status"),
        "logs": state.get("logs", []),
    }


@app.post("/api/campaign/{campaign_id}/reference-image")
async def submit_reference_image(
    campaign_id: str,
    asset_id: str = Form(..., description="asset_id from pending_reference_requests"),
    file: UploadFile = File(...),
):
    """
    Business uploads a real reference photo (product shot, founder
    photo, etc.) for one asset that image_generation_router flagged as
    pending. Regenerates just that asset via image-to-image, WITHOUT
    forcing a full campaign replan.
    """
    manager = get_manager()
    image_bytes = await file.read()
    try:
        result = manager.submit_reference_image(campaign_id, asset_id, image_bytes)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"status": "generated", "asset_id": asset_id, "result": result}


@app.post("/api/chat")
def chat(request: ChatRequest):
    """
    Chat-based frontend entry point. Routes free-text messages through
    an LLM function-calling layer (chat_router.py) that calls the SAME
    CampaignManager methods the structured endpoints above use — this
    isn't a parallel implementation, it's a translator in front of the
    existing pipeline.

    Returns a text reply plus a list of executed tool_calls, each with
    its arguments and result — render `reply` as a chat bubble and each
    tool_call as its own inline card (e.g. a "Campaign Started" card
    for start_campaign, a status card for get_campaign_status).
    """
    try:
        result = chat_router.handle_chat_message(
            [m.model_dump() for m in request.messages]
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chat routing failed: {str(e)}")


@app.get("/api/campaign/{campaign_id}/timeline")
def campaign_timeline(campaign_id: str, limit: int = 200):
    """
    Event history for one campaign — chat-triggered actions AND
    cron-triggered daily publishes, in one unified feed. This is what
    a per-campaign Hub view renders.
    """
    return {"campaign_id": campaign_id, "events": campaign_events.get_timeline(campaign_id, limit=limit)}


@app.get("/api/hub")
def hub_feed(limit: int = 100):
    """Cross-campaign activity feed for a Hub landing page."""
    return {"events": campaign_events.get_all_recent_events(limit=limit)}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
