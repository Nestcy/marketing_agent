# ---------------------------------------------------------
# FastAPI Web Application & API Server for Marketing Engine
# Deployed on Railway (web + worker + beat services)
# ---------------------------------------------------------
import os
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from campaign_manager import CampaignManager
from config import ASSETS_DIR
import chat_router
import campaign_events

app = FastAPI(
    title="Autonomous Marketing Engine API",
    description=(
        "Backend API powering AI business research (Tavily + Firecrawl), "
        "day-by-day content calendar planning, per-day content generation, "
        "and organic crossposting — with explicit human approval gates at "
        "both the plan level and the individual-post level."
    ),
    version="3.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")

_campaign_manager: Optional[CampaignManager] = None


def get_manager() -> CampaignManager:
    global _campaign_manager
    if _campaign_manager is None:
        _campaign_manager = CampaignManager()
    return _campaign_manager


# ---------------------------------------------------------
# Request & Response Schemas
# ---------------------------------------------------------
class StartCampaignRequest(BaseModel):
    campaign_id: str = Field(..., example="acme-coffee-shop")
    business_context: str = Field(..., example="A neighborhood coffee shop known for single-origin pour-overs.")
    campaign_goal: str = Field(..., example="Increase foot traffic and build a loyal local following.")
    target_audience: str = Field(..., example="Young professionals and students nearby.")
    timeframe_days: int = Field(30, description="30 or 90 — how many days of content to plan.")
    user_plan: str = Field("free", description="'free' (Gemini free-tier images) or 'paid' (DALL-E 3 / Stable Diffusion).")
    business_website_url: Optional[str] = None
    facebook_page_url: Optional[str] = None


class RefineRequest(BaseModel):
    feedback: str = Field(..., example="Less product-focused, more lifestyle/testimonial content.")


class TweakRequest(BaseModel):
    feedback: str = Field(..., example="Make the caption punchier and shorter.")


class ChatMessage(BaseModel):
    role: str = Field(..., example="user")
    content: str


class ChatRequest(BaseModel):
    messages: List[ChatMessage]


# ---------------------------------------------------------
# Health
# ---------------------------------------------------------
@app.get("/")
def root():
    return {"status": "online", "service": "Autonomous Marketing Engine API", "version": "3.0.0", "docs_url": "/docs"}


@app.get("/health")
def health_check():
    return {"status": "ok"}


# ---------------------------------------------------------
# PLAN gate
# ---------------------------------------------------------
@app.post("/api/campaign/start")
def start_campaign(request: StartCampaignRequest):
    """
    Kicks off business research + a full day-by-day calendar draft in
    the background. Lands in plan_status="draft" — poll
    /api/campaign/{id}/status, then approve or refine the plan before
    any content generation begins.
    """
    manager = get_manager()
    manager.start_campaign(request.campaign_id, request.model_dump())
    return {"status": "started", "campaign_id": request.campaign_id}


@app.post("/api/campaign/{campaign_id}/plan/approve")
def approve_plan(campaign_id: str):
    """Locks in the draft calendar. Daily generation only proceeds once approved."""
    manager = get_manager()
    try:
        manager.approve_plan(campaign_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "approved", "campaign_id": campaign_id}


@app.post("/api/campaign/{campaign_id}/plan/refine")
def refine_plan(campaign_id: str, request: RefineRequest):
    """Records feedback as a learned preference and regenerates the whole calendar. Stays in draft."""
    manager = get_manager()
    try:
        manager.refine_plan(campaign_id, request.feedback)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"status": "refining", "campaign_id": campaign_id}


# ---------------------------------------------------------
# DAY gate
# ---------------------------------------------------------
@app.post("/api/campaign/{campaign_id}/day/{date}/generate")
def generate_day(campaign_id: str, date: str):
    """
    Manually trigger generation for a specific day (normally the daily
    cron does this automatically once the plan is approved). Useful for
    testing or catching up a missed day.
    """
    manager = get_manager()
    try:
        result = manager.generate_day_asset(campaign_id, date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@app.post("/api/campaign/{campaign_id}/day/{date}/approve")
def approve_day(campaign_id: str, date: str):
    """
    Marks this day's draft as approved/ready. This platform doesn't
    crosspost yet — that's a separate feature to be added later on the
    frontend side. Approval means "this ad is finished," not "posted."
    """
    manager = get_manager()
    try:
        result = manager.approve_day(campaign_id, date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@app.post("/api/campaign/{campaign_id}/day/{date}/tweak")
def tweak_day(campaign_id: str, date: str, request: TweakRequest):
    """Records feedback and regenerates just this one day's draft. Does not publish."""
    manager = get_manager()
    try:
        result = manager.tweak_day(campaign_id, date, request.feedback)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@app.post("/api/campaign/{campaign_id}/day/{date}/reference-image")
async def submit_reference_image(campaign_id: str, date: str, file: UploadFile = File(...)):
    """Business uploads a real reference photo for a day that needs one. Regenerates that day's image."""
    manager = get_manager()
    image_bytes = await file.read()
    try:
        result = manager.submit_reference_image(campaign_id, date, image_bytes)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"status": "generated", "date": date, "result": result}


# ---------------------------------------------------------
# Reads
# ---------------------------------------------------------
@app.get("/api/campaign/{campaign_id}/status")
def campaign_status(campaign_id: str):
    manager = get_manager()
    state = manager.get_status(campaign_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"No campaign found for campaign_id={campaign_id!r}")
    return {
        "campaign_id": campaign_id,
        "is_running": manager.is_running(campaign_id),
        "plan_status": state.get("plan_status"),
        "calendar_plan": state.get("calendar_plan"),
        "generated_captions": state.get("generated_captions"),
        "generated_images": state.get("generated_images"),
        "asset_status": state.get("asset_status"),
        "logs": state.get("logs", []),
    }


@app.get("/api/campaign/{campaign_id}/timeline")
def campaign_timeline(campaign_id: str, limit: int = 200):
    return {"campaign_id": campaign_id, "events": campaign_events.get_timeline(campaign_id, limit=limit)}


@app.get("/api/hub")
def hub_feed(limit: int = 100):
    return {"events": campaign_events.get_all_recent_events(limit=limit)}


# ---------------------------------------------------------
# Chat
# ---------------------------------------------------------
@app.post("/api/chat")
def chat(request: ChatRequest):
    try:
        return chat_router.handle_chat_message([m.model_dump() for m in request.messages])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chat routing failed: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
