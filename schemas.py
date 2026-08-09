# ---------------------------------------------------------
# schemas.py
# Pydantic models for structured LLM output via ChatGroq's
# .with_structured_output(). Kept intentionally flat/simple —
# nested, multi-field schemas were the likely cause of Groq
# tool_use_failed errors seen with more complex shapes.
# ---------------------------------------------------------

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class DayPlanEntry(BaseModel):
    date: str = Field(..., description="ISO date string for this day, e.g. 2026-08-10")
    idea: str = Field(..., description="The content idea/concept for this day's post.")
    platform: str = Field(..., description="Which platform this post targets, e.g. instagram, facebook, tiktok.")
    needs_reference_photo: bool = Field(
        ..., description="True if this idea requires a real business-supplied photo (product, founder, team, store) rather than a purely AI-imagined image."
    )


class CalendarPlan(BaseModel):
    """
    A LIST of day entries (each with its own date field), not a dict
    keyed by date — a dict with dynamic string keys converts to a JSON
    schema shape that tool-calling backends handle unreliably at scale
    (this caused empty tool_use_failed errors at 30-90 entries in
    testing). A flat list of fixed-shape objects is the reliable
    pattern; marketing_engine.py converts it to a date-keyed dict after
    the structured call returns.
    """
    days: List[DayPlanEntry] = Field(..., min_length=1)


class PlannerOutput(BaseModel):
    calendar_plan: CalendarPlan


class DayContentOutput(BaseModel):
    """Output for generating a single day's caption (image is handled separately via image_clients)."""
    caption: str = Field(..., max_length=280, description="The post caption/copy for this day.")
    image_prompt: str = Field(..., description="A detailed text-to-image prompt describing the visual for this day's post.")
