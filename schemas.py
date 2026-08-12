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
        ..., description="True if today's idea would land better with a real business-supplied photo (product, founder, team, venue) rather than a generic/stock visual. Informational only — flags to the business that they should shoot or supply their own photo for this one."
    )
    caption: str = Field(..., max_length=280, description="The primary post caption/copy for today.")
    ad_copy_variants: List[str] = Field(
        ..., min_length=2, max_length=3,
        description="2-3 alternative short ad copy variants for today's idea (different angles/hooks on the same idea), for the business to A/B test or pick from."
    )
    image_prompt: str = Field(
        ..., description="A detailed, ready-to-use text-to-image prompt (subject, style, composition, lighting, mood) describing the visual for today's post, meant to be pasted into an image generation tool of the business's choice."
    )

