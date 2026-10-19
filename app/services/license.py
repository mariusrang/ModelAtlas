"""License check: can a company use this model commercially?

The facts (which license each model and dataset declares, who descends from
whom) are VERIFIED. The verdict is DERIVED with one simple, fail-safe rule:

- each license on the lineage gets a status from config/licenses.toml
  (commercial_use true -> OK, "conditional" -> CONDITIONAL, false -> NOT_ALLOWED,
  not in our table or no license at all -> NEEDS_REVIEW);
- the verdict is the WORST status over the model and all its ancestors,
  because a fine-tune can't remove the restrictions of the model it comes from;
- training data is weaker evidence: whether weights trained on non-commercial
  data inherit that restriction is legally unsettled, so a non-commercial or
  conditional dataset never makes the verdict "not_allowed", it only stops it
  from being better than NEEDS_REVIEW. Datasets with no license don't count
  (most dataset cards declare none, it would flag almost every model).
"""

from dataclasses import dataclass, field

from app.config import Settings
from app.domain import relation_from_type
from app.errors import NotFoundError
from app.models.catalog import LicenseInfo
from app.models.license import AncestorLicense, DatasetLicense, LicenseCheckResponse, LicenseConflict, Verdict
from app.repositories.graph_repository import GraphRepository
from app.services.common import check_depth

SEVERITY = [Verdict.OK, Verdict.CONDITIONAL, Verdict.NEEDS_REVIEW, Verdict.NOT_ALLOWED]


@dataclass
class LineageFacts:
    """Everything the rule looks at, read from the graph."""

    own: LicenseInfo | None
    ancestors: list[AncestorLicense] = field(default_factory=list)
    datasets: list[DatasetLicense] = field(default_factory=list)
    lineage_depth: int | None = None  # longest chain above the model, precomputed by the ETL


def license_status(license_id: str | None, commercial_use) -> Verdict:
    """Status of a single license."""
    if license_id is None:
        return Verdict.NEEDS_REVIEW
    if commercial_use is True:
        return Verdict.OK
    if commercial_use is False:
        return Verdict.NOT_ALLOWED
    if commercial_use == "conditional":
        return Verdict.CONDITIONAL
    return Verdict.NEEDS_REVIEW  # license we have no rule for (family "unknown")


def worst(statuses: list[Verdict]) -> Verdict:
    """Most restrictive status of the list (OK for an empty list)."""
    return max(statuses, key=SEVERITY.index, default=Verdict.OK)


def restricted_datasets(datasets: list[DatasetLicense]) -> list[DatasetLicense]:
    """Datasets whose declared license is non-commercial or conditional."""
    return [d for d in datasets if d.license and d.commercial_use in (False, "conditional")]


def facts_from_row(row: dict) -> LineageFacts:
    """Cypher row of license_check.cypher -> LineageFacts (lists sorted closest first)."""
    ancestors = [
        AncestorLicense(
            id=r["id"],
            distance=r["distance"],
            path=r["path"],
            relations=[relation_from_type(t) for t in r["rel_types"]],
            license=r["license"],
            family=r["family"],
            commercial_use=r["commercial_use"],
            unlisted=r["unlisted"],
        )
        for r in row["ancestors"]
    ]
    datasets = [DatasetLicense(**d) for d in row.get("datasets") or []]
    return LineageFacts(
        own=LicenseInfo(**row["license"]) if row["license"] else None,
        ancestors=sorted(ancestors, key=lambda a: (a.distance, a.id)),
        datasets=sorted(datasets, key=lambda d: (d.distance, d.id)),
        lineage_depth=row.get("lineage_depth"),
    )


def evaluate(model_id: str, facts: LineageFacts, depth_limit: int) -> LicenseCheckResponse:
    """Apply the rule. Pure function: no database, easy to unit test."""
    own, ancestors = facts.own, facts.ancestors
    inherited = next((a for a in ancestors if a.license), None) if own is None else None
    statuses = [license_status(a.license, a.commercial_use) for a in ancestors]
    if own is not None:
        own_status = license_status(own.id, own.commercial_use)
        verdict = worst([own_status] + statuses)
    elif inherited is not None:
        own_status = None
        verdict = worst(statuses)  # no own license: judged on its lineage only
    else:
        own_status, verdict = None, Verdict.NEEDS_REVIEW
    if restricted_datasets(facts.datasets):
        verdict = worst([verdict, Verdict.NEEDS_REVIEW])

    conflicts = []
    if own_status is not None:
        for anc in ancestors:
            status = license_status(anc.license, anc.commercial_use)
            if anc.license and SEVERITY.index(status) > SEVERITY.index(own_status):
                conflicts.append(
                    LicenseConflict(
                        ancestor=anc,
                        reason=f"declares {own.id} but descends from {anc.id} ({anc.license}: {status.value})",
                    )
                )
    too_deep = (facts.lineage_depth or 0) > depth_limit
    return LicenseCheckResponse(
        model_id=model_id,
        declared_license=own,
        verdict=verdict,
        explanation=explain(facts, inherited, verdict, len(conflicts), depth_limit),
        inherited_from=inherited.id if inherited else None,
        conflicts=conflicts,
        ancestors=ancestors,
        training_data=facts.datasets,
        depth_limit=depth_limit,
        incomplete_lineage=too_deep or any(a.unlisted for a in ancestors),
    )


def explain(facts: LineageFacts, inherited, verdict: Verdict, conflicts: int, depth_limit: int) -> str:
    """One readable paragraph, built only from the facts above."""
    own, ancestors = facts.own, facts.ancestors
    if own is not None:
        parts = [f"The model declares {own.id} ({own.family or 'unknown'} family)."]
    elif inherited is not None:
        parts = [
            f"The model declares no license; the closest licensed ancestor is {inherited.id} ({inherited.license})."
        ]
    else:
        parts = ["Neither the model nor any ancestor declares a license."]
    parts.append(f"{len(ancestors)} ancestor(s) checked." if ancestors else "No ancestor: it is a root model.")
    if conflicts:
        parts.append(f"{conflicts} ancestor(s) have a stricter license than the one declared.")
    missing = sum(1 for a in ancestors if not a.license)
    if missing:
        parts.append(f"{missing} ancestor(s) declare no license, which needs a human check.")
    restricted = restricted_datasets(facts.datasets)
    if restricted:
        names = ", ".join(sorted({d.id for d in restricted})[:3])
        parts.append(f"{len(restricted)} training dataset(s) have a non-commercial or conditional license ({names}).")
    if any(a.unlisted for a in ancestors):
        parts.append("Part of the lineage is not in the public dump, so it can't be fully checked.")
    if (facts.lineage_depth or 0) > depth_limit:
        parts.append(f"The lineage goes deeper than {depth_limit} generations; higher ancestors were not checked.")
    parts.append(f"Verdict: {verdict.value}.")
    return " ".join(parts)


class LicenseService:
    """Wraps evaluate() with the database read."""

    def __init__(self, repo: GraphRepository, settings: Settings):
        self.repo = repo
        self.settings = settings

    def _depth(self, depth: int | None) -> int:
        return check_depth(depth or self.settings.max_traversal_depth, self.settings)

    def check(self, model_id: str, depth: int | None = None) -> LicenseCheckResponse:
        """License check for one model, ancestors up to `depth` generations."""
        depth = self._depth(depth)
        row = self.repo.license_check(model_id, depth)
        if row is None:
            raise NotFoundError(f"model '{model_id}' is not in the graph")
        return evaluate(model_id, facts_from_row(row), depth)

    def check_many(self, model_ids: list[str], depth: int | None = None) -> dict[str, LicenseCheckResponse]:
        """License check for several models in one query (unknown ids are left out)."""
        depth = self._depth(depth)
        rows = self.repo.license_check_many(model_ids, depth) if model_ids else []
        return {row["id"]: evaluate(row["id"], facts_from_row(row), depth) for row in rows}
