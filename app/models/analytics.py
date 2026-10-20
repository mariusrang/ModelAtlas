"""Schemas for the graph-algorithm endpoints (results of `make gds`, all DERIVED)."""

from pydantic import BaseModel, Field

from app.domain import Evidence
from app.models.catalog import ModelSummary


class RankedModel(BaseModel):
    """A model and its PageRank on the lineage graph."""

    model: ModelSummary
    pagerank: float = Field(
        description="PageRank on lineage edges (child -> parent): how much the ecosystem builds on it"
    )
    descendant_count: int | None = None
    family_id: int | None = None


class InfluentialResponse(BaseModel):
    """Most influential models, optionally for one task."""

    evidence: Evidence = Evidence.DERIVED
    algorithm: str = "PageRank (GDS) on FINETUNED_FROM / ADAPTER_OF / QUANTIZED_FROM / MERGED_FROM"
    task: str | None = None
    results: list[RankedModel]


class SimilarModel(BaseModel):
    """A model trained on the same data or citing the same papers."""

    model: ModelSummary
    score: float = Field(description="Jaccard similarity of the declared datasets and papers (0 to 1)")
    shared_datasets: int
    shared_papers: int


class SimilarResponse(BaseModel):
    """Models similar to the requested one."""

    model_id: str
    evidence: Evidence = Evidence.DERIVED
    algorithm: str = "Node Similarity (GDS, Jaccard) on TRAINED_ON and CITES, top 5 per model, score >= 0.3"
    results: list[SimilarModel]
    note: str | None = None


class FamilyMember(BaseModel):
    """A member of the same community."""

    id: str
    downloads: int | None = None
    pagerank: float | None = None


class FamilyResponse(BaseModel):
    """Where a model sits in the ecosystem."""

    model_id: str
    evidence: Evidence = Evidence.DERIVED
    pagerank: float
    family_id: int = Field(description="WCC component: every model connected to this one by lineage")
    family_size: int
    rank_in_family: int = Field(description="1 = highest PageRank of the family")
    community: int = Field(description="Louvain community: a tighter sub-family inside the WCC family")
    community_size: int
    top_members: list[FamilyMember] = Field(description="Most influential models of the community")
