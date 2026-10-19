"""Schemas for dataset pages (data from the dataset cards, our second source)."""

from pydantic import BaseModel, Field

from app.domain import Evidence
from app.models.catalog import LicenseInfo, ModelSummary


class DatasetDetail(BaseModel):
    """A training dataset and the models trained on it."""

    id: str
    evidence: Evidence = Evidence.VERIFIED
    listed: bool = Field(description="False when the dataset is cited by models but has no public card")
    author: str | None = None
    downloads: int | None = None
    likes: int | None = None
    created_at: str | None = None
    size_category: str | None = Field(default=None, examples=["10K<n<100K"])
    task_categories: list[str] = []
    license_info: LicenseInfo | None = None
    languages: list[str] = []
    model_count: int = Field(description="Models in the graph that declare this dataset")
    top_models: list[ModelSummary]
