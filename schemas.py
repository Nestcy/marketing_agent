# ---------------------------------------------------------
# schemas.py
# Pydantic models for structured LLM output via generate_structured().
#
# Deliberately two small, flat schemas rather than one big one:
#   - StrategyOutline: a handful of fields, ONE call for a 3-day (3d)
#     content calendar — this keeps token use low and respects rate limits.
#   - DayContentOutput: the day's idea/platform/reference-need AND its
#     caption/image_prompt, combined into a single call, made once per
#     day (only when that day is actually about to be generated).
# ---------------------------------------------------------

from typing import List, Optional

from pydantic import BaseModel, Field


class StrategyOutline(BaseModel):
    content_pillars: List[str] = Field(
        ..., min_length=3, max_length=6,
        description="3-6 recurring content themes to rotate through, e.g. 'Product highlights', 'Behind the scenes', 'Customer testimonials', 'Educational tips', 'Promotions'."
    )
    tone: str = Field(..., description="Overall brand voice/tone for captions, e.g. 'warm, casual, a little playful'.")
    platform_mix: str = Field(
        ..., description="Which platforms to prioritize and roughly how often, e.g. 'Instagram most days, Facebook a couple times a week, TikTok occasionally'."
    )
    notes: Optional[str] = Field(None, description="Any other short strategic guidance.")


class PlannerOutput(BaseModel):
    strategy_outline: StrategyOutline


class DayContentOutput(BaseModel):
    idea: str = Field(..., description="Today's specific content idea, drawn from the strategy's content pillars — should not repeat a recently-used idea.")
    platform: str = Field(..., description="Which single platform today's post targets, e.g. instagram, facebook, tiktok.")
    needs_reference_photo: bool = Field(
        ..., description="True if today's idea requires a real business-supplied reference photo (product, founder, team, venue) to render a personalized style image accurately."
    )
    caption: str = Field(..., max_length=280, description="The post caption/copy for today.")
    image_prompt: str = Field(..., description="A detailed text-to-image prompt describing the visual for today's post.")

