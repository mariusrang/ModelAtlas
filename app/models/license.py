"""Schemas for the license check."""

from enum import Enum

from pydantic import BaseModel, Field

from app.domain import Evidence, Relation
from app.models.catalog import LicenseInfo


class Verdict(str, Enum):
    """Final answer to "can a company use this model commercially?"."""

    OK = "commercial_use_ok"
    CONDITIONAL = "conditional"
    NEEDS_REVIEW = "needs_review"
    NOT_ALLOWED = "not_allowed"


class AncestorLicense(BaseModel):
    """An ancestor of the checked model and the license it declares (VERIFIED)."""

    id: str
    distance: int
    path: list[str] = Field(description="Model ids from the checked model up to this ancestor")
    relations: list[Relation]
    license: str | None = None
    family: str | None = None
    commercial_use: bool | str | None = None
    unlisted: bool = False
    evidence: Evidence = Evidence.VERIFIED


class DatasetLicense(BaseModel):
    """A training dataset of the model or of one of its ancestors (from the dataset cards)."""

    id: str
    used_by: str = Field(description="The model on the lineage that declares this dataset")
    distance: int = Field(description="0 = the checked model itself, 1 = its parent...")
    listed: bool = Field(description="False when the dataset has no public card, so its license is unknown")
    license: str | None = None
    family: str | None = None
    commercial_use: bool | str | None = None
    evidence: Evidence = Evidence.VERIFIED


class LicenseConflict(BaseModel):
    """An ancestor whose license is stricter than what the model declares."""

    ancestor: AncestorLicense
    reason: str


class LicenseCheckResponse(BaseModel):
    """Result of walking every ancestor's license."""

    model_id: str
    declared_license: LicenseInfo | None = Field(description="VERIFIED: what the model card says")
    verdict: Verdict = Field(description="DERIVED: our rule applied to every license on the lineage")
    verdict_evidence: Evidence = Evidence.DERIVED
    explanation: str
    inherited_from: str | None = Field(default=None, description="Nearest ancestor with a license, if none declared")
    conflicts: list[LicenseConflict] = []
    ancestors: list[AncestorLicense] = []
    training_data: list[DatasetLicense] = Field(
        default=[], description="Datasets declared by the model and its ancestors, with their license"
    )
    depth_limit: int
    incomplete_lineage: bool = Field(
        description="True if an ancestor is not in the public dump (Unlisted) or is further up than depth_limit"
    )
    disclaimer: str = (
        "Informational only, not legal advice. Licenses are self-declared on Hugging Face and our rules "
        "(config/licenses.toml) are a simplified reading; always read the actual license text."
    )
