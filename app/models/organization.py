"""Schemas for organization pages."""

from pydantic import BaseModel, Field

from app.domain import Evidence


class Builder(BaseModel):
    """An organization that publishes models derived from another organization's models."""

    organization: str
    models: int


class FootprintResponse(BaseModel):
    """How much an organization's models are reused by the rest of the ecosystem."""

    organization: str
    evidence: Evidence = Evidence.DERIVED
    own_models: int
    own_downloads: int
    derived_models: int = Field(description="Distinct models derived from theirs, within depth_limit generations")
    depth_limit: int
    top_builders: list[Builder]
    direct_children_by_kind: dict[str, int]
