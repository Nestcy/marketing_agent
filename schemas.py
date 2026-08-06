# ---------------------------------------------------------
# schemas.py
# Pydantic models for structured LLM output via ChatGroq's
# .with_structured_output(). Kept intentionally flat/simple —
# nested, multi-field schemas were the likely cause of Groq
# tool_use_failed errors seen with more complex shapes.
# ---------------------------------------------------------

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class DayPlan(BaseModel):
    idea: str = Field(..., description="The content idea/concept for this day's post.")
    platform: str = Field(..., description="Which platform this post targets, e.g. instagram, facebook, tiktok.")
    needs_reference_photo: bool = Field(
        ..., description="True if this idea requires a real business-supplied photo (product, founder, team, store) rather than a purely AI-imagined image."
    )


class CalendarPlan(BaseModel):
    """
    Keyed by ISO date string ("2026-08-10"), one entry per day of the
    requested timeframe. Using a flat Dict[str, DayPlan] rather than a
    deeply nested structure keeps this easy for the model to produce
    reliably via tool-calling.
    """
    days: Dict[str, DayPlan] = Field(..., min_length=1)


class PlannerOutput(BaseModel):
    calendar_plan: CalendarPlan


class DayContentOutput(BaseModel):
    """Output for generating a single day's caption (image is handled separately via image_clients)."""
    caption: str = Field(..., max_length=280, description="The post caption/copy for this day.")
    image_prompt: str = Field(..., description="A detailed text-to-image prompt describing the visual for this day's post.")
