"""Schemas for the model recommendation (the "client brief")."""

from pydantic import BaseModel, Field

from app.domain import Evidence
from app.models.catalog import ModelSummary
from app.models.license import Verdict


class Recommendation(BaseModel):
    """One recommended model with the reasons it was picked."""

    model: ModelSummary
    score: float = Field(description="DERIVED: log10(downloads+1) + 0.5 * log10(likes+1)")
    license_verdict: Verdict | None = Field(default=None, description="DERIVED, only when commercial=true")
    reasons: list[str]
    evidence: Evidence = Evidence.DERIVED


class RecommendResponse(BaseModel):
    """Ranked shortlist for a client brief."""

    brief: dict
    results: list[Recommendation]
    considered: int = Field(description="How many candidates matched the filters before ranking")


class RecommendRequest(BaseModel):
    """A client brief. Only `task` is required."""

    task: str = Field(examples=["text-generation"], description="Hugging Face pipeline tag")
    max_params_b: float | None = Field(default=None, gt=0, examples=[8], description="Size limit in billions")
    language: str | None = Field(default=None, examples=["fr"], description="ISO code the model must declare")
    commercial_use: bool = Field(default=False, description="Keep only models whose whole lineage allows it")
    min_downloads: int | None = Field(default=None, ge=0)
    limit: int = Field(default=5, ge=1, le=20)
