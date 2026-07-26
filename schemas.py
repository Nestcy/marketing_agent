# ---------------------------------------------------------
# schemas.py
# Pydantic models for structured LLM output. Used with
# ChatGroq's .with_structured_output() so the model is
# constrained to return a validated shape instead of us
# hoping a raw JSON string parses correctly.
# ---------------------------------------------------------

from typing import Dict, List

from pydantic import BaseModel, Field, field_validator


class CampaignPlan(BaseModel):
    week_1: List[str] = Field(..., min_length=1, description="Assets planned for week 1")
    week_2: List[str] = Field(..., min_length=1, description="Assets planned for week 2")
    week_3: List[str] = Field(..., min_length=1, description="Assets planned for week 3")
    week_4: List[str] = Field(..., min_length=1, description="Assets planned for week 4")

    @field_validator("week_1", "week_2", "week_3", "week_4")
    @classmethod
    def each_asset_declares_type(cls, assets: List[str]) -> List[str]:
        for asset in assets:
            if "image" not in asset.lower() and "video" not in asset.lower():
                raise ValueError(
                    f"Asset description {asset!r} must mention 'Image' or 'Video' "
                    f"so downstream routers can detect the asset type."
                )
        return assets


class BudgetAllocations(BaseModel):
    facebook_ads: float = Field(..., ge=0)
    tiktok_ads: float = Field(..., ge=0)
    google_ads: float = Field(..., ge=0)


class PlannerOutput(BaseModel):
    campaign_plan: CampaignPlan
    budget_allocations: BudgetAllocations

    def validate_budget_matches(self, total_budget: float, tolerance: float = 1.0) -> None:
        """
        Extra check beyond field-level validation: the three channel
        budgets should sum to (approximately) the total budget the
        business gave us. Raises ValueError if they don't, which the
        calling node treats the same as any other LLM failure — falls
        back to the mock plan rather than silently misallocating spend.
        """
        total = (
            self.budget_allocations.facebook_ads
            + self.budget_allocations.tiktok_ads
            + self.budget_allocations.google_ads
        )
        if abs(total - total_budget) > tolerance:
            raise ValueError(
                f"Budget allocations sum to {total:.2f}, expected ~{total_budget:.2f}"
            )


class IdeationOutput(BaseModel):
    generated_copy: Dict[str, str] = Field(..., min_length=1)

    @field_validator("generated_copy")
    @classmethod
    def copy_not_too_long(cls, copy: Dict[str, str]) -> Dict[str, str]:
        for asset_id, text in copy.items():
            if len(text) > 220:
                raise ValueError(f"Copy for {asset_id!r} exceeds 220 characters ({len(text)})")
        return copy
