# ---------------------------------------------------------
# FastAPI Web Application & API Server for Marketing Engine
# Designed for deployment on Railway / Render / Fly.io
# Connects to Lovable Frontend via REST API with CORS
# ---------------------------------------------------------
import os
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from marketing_engine import build_graph, MarketingState
from config import ASSETS_DIR

app = FastAPI(
    title="Autonomous Marketing Engine API",
    description="Backend API powering AI campaign planning, multimodal content generation, and ad platform publishing.",
    version="1.0.0",
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

# Compile the LangGraph app once on startup
graph_app = build_graph()


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


class CampaignResponse(BaseModel):
    status: str
    campaign_plan: Optional[Dict[str, Any]]
    budget_allocations: Optional[Dict[str, float]]
    generated_copy: Optional[Dict[str, str]]
    generated_images: Optional[Dict[str, Any]]
    generated_videos: Optional[Dict[str, Any]]
    publishing_status: Optional[Dict[str, Any]]
    logs: List[str]


# ---------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------
@app.get("/")
def root():
    """Health check endpoint for Railway."""
    return {
        "status": "online",
        "service": "Autonomous Marketing Engine API",
        "version": "1.0.0",
        "docs_url": "/docs",
    }


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/api/campaign/run", response_model=CampaignResponse)
def run_campaign(request: CampaignRequest):
    """
    Triggers the complete autonomous marketing pipeline:
      1. Strategic Planning & Budget Allocation
      2. Ad Copy & Prompt Ideation
      3. Parallel Image (DALL-E 3 / SD3) & Video (HeyGen / Runway) Generation
      4. Publishing to Facebook, TikTok, and Google Ads APIs
    """
    initial_state = {
        "business_context": request.business_context,
        "campaign_goal": request.campaign_goal,
        "target_audience": request.target_audience,
        "total_budget": request.total_budget,
        "logs": [],
    }

    try:
        final_state = {}
        for output in graph_app.stream(initial_state):
            for node_name, state_chunk in output.items():
                final_state.update(state_chunk)

        # Retrieve cumulative state values
        cumulative_state = graph_app.get_state({"configurable": {"thread_id": "default"}}).values

        return CampaignResponse(
            status="completed",
            campaign_plan=final_state.get("campaign_plan") or cumulative_state.get("campaign_plan"),
            budget_allocations=final_state.get("budget_allocations") or cumulative_state.get("budget_allocations"),
            generated_copy=final_state.get("generated_copy") or cumulative_state.get("generated_copy"),
            generated_images=final_state.get("generated_images") or cumulative_state.get("generated_images"),
            generated_videos=final_state.get("generated_videos") or cumulative_state.get("generated_videos"),
            publishing_status=final_state.get("publishing_status") or cumulative_state.get("publishing_status"),
            logs=final_state.get("logs", []),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Campaign execution failed: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
