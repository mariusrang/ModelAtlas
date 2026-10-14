"""Schemas for family trees: lineage, descendants and model-to-model connections."""

from pydantic import BaseModel, Field

from app.domain import Evidence
from app.models.catalog import ModelSummary


class PathNode(BaseModel):
    """A model on a lineage path."""

    id: str
    downloads: int | None = None
    unlisted: bool = False


class LineagePath(BaseModel):
    """One chain from the model up to a root. relations[i] links nodes[i] to nodes[i+1]."""

    nodes: list[PathNode]
    relations: list[str] = Field(examples=[["quantized", "finetune"]])


class LineageResponse(BaseModel):
    """Every path from a model to its foundation model(s)."""

    model_id: str
    evidence: Evidence = Evidence.VERIFIED
    depth_limit: int
    paths: list[LineagePath]
    roots: list[str] = Field(description="Distinct root models reached")
    truncated: bool = Field(description="True if we hit max_paths; merges can multiply paths")


class DescendantItem(BaseModel):
    """A model derived from the requested one."""

    model: ModelSummary
    distance: int = Field(description="Generations below the requested model")
    relation: str = Field(description="How this model relates to its own parent on the path")


class DescendantPage(BaseModel):
    """One page of descendants, most downloaded first."""

    model_id: str
    evidence: Evidence = Evidence.VERIFIED
    items: list[DescendantItem]
    next_cursor: str | None = Field(description="Pass it as ?cursor= to get the next page; null on the last page")


class CommonAncestor(BaseModel):
    """Closest model both sides descend from."""

    id: str
    downloads: int | None = None
    distance_from_a: int
    distance_from_b: int


class ConnectionResponse(BaseModel):
    """How two models are related."""

    model_a: str
    model_b: str
    evidence: Evidence = Evidence.DERIVED
    common_ancestor: CommonAncestor | None = None
    shortest_path: LineagePath | None = None
    shared_datasets: list[str] = []
    shared_papers: list[str] = []
    same_organization: bool = False
    summary: str
