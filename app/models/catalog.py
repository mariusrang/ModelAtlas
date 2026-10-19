"""Schemas for search, stats and the model page."""

from pydantic import BaseModel, Field

from app.domain import Evidence, Relation


class ModelSummary(BaseModel):
    """The few fields we show every time a model appears in a list."""

    id: str = Field(examples=["mistralai/Mistral-7B-v0.1"])
    author: str | None = None
    downloads: int | None = None
    likes: int | None = None
    task: str | None = Field(default=None, examples=["text-generation"])
    params_b: float | None = Field(default=None, description="Size in billions of parameters, guessed from the name")
    params_estimated: bool | None = None
    unlisted: bool = Field(default=False, description="True when the model is not in the public Hub dump")
    license: str | None = None


class SearchHit(BaseModel):
    """One search result."""

    model: ModelSummary
    rank: float = Field(description="Fulltext score boosted by log(downloads)")


class SearchResponse(BaseModel):
    """Search results, best first."""

    query: str
    hits: list[SearchHit]


class StatsResponse(BaseModel):
    """Graph size."""

    models: int
    unlisted: int
    organizations: int
    licenses: int
    datasets: int
    papers: int
    lineage_edges: dict[str, int] = Field(examples=[{"finetune": 36907, "quantized": 47560}])


class ParentRef(BaseModel):
    """A declared parent of a model."""

    id: str
    relation: Relation
    declared: bool = Field(description="False when the card gave a parent but not the relation kind")
    unlisted: bool = False


class LicenseInfo(BaseModel):
    """A license node and the rules we attached to it (config/licenses.toml)."""

    id: str
    family: str | None = None
    commercial_use: bool | str | None = None
    url: str | None = None
    notes: str | None = None


class ModelDetail(BaseModel):
    """Everything on a model page. All of it is VERIFIED (declared in the card),
    except lineage_depth / descendant_count / root_ids, which we DERIVE at load time."""

    model: ModelSummary
    evidence: Evidence = Evidence.VERIFIED
    organization: str | None = None
    license_info: LicenseInfo | None = None
    library: str | None = None
    created_at: str | None = None
    formats: list[str] = []
    datasets: list[str] = []
    papers: list[str] = []
    languages: list[str] = []
    architectures: list[str] = []
    parents: list[ParentRef] = []
    lineage_depth: int | None = Field(default=None, description="DERIVED: longest chain of parents above")
    child_count: int | None = None
    descendant_count: int | None = Field(default=None, description="DERIVED: distinct models derived from it")
    root_ids: list[str] = Field(default=[], description="DERIVED: foundation models it descends from")
