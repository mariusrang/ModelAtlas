"""License check: can a company use this model commercially?

The facts (which license each model declares, who descends from whom) are
VERIFIED. The verdict is DERIVED with one simple, fail-safe rule:

- each license on the lineage gets a status from config/licenses.toml
  (commercial_use true -> OK, "conditional" -> CONDITIONAL, false -> NOT_ALLOWED,
  not in our table or no license at all -> NEEDS_REVIEW);
- the verdict is the WORST status over the model and all its ancestors,
  because a fine-tune can't remove the restrictions of the model it comes from.
"""

from app.config import Settings
from app.domain import relation_from_type
from app.errors import NotFoundError
from app.models.catalog import LicenseInfo
from app.models.license import AncestorLicense, LicenseCheckResponse, LicenseConflict, Verdict
from app.repositories.graph_repository import GraphRepository
from app.services.common import check_depth

SEVERITY = [Verdict.OK, Verdict.CONDITIONAL, Verdict.NEEDS_REVIEW, Verdict.NOT_ALLOWED]


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


def build_ancestors(rows: list[dict]) -> list[AncestorLicense]:
    """Cypher rows -> AncestorLicense, closest first."""
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
        for r in rows
    ]
    return sorted(ancestors, key=lambda a: (a.distance, a.id))


def evaluate(model_id: str, own: LicenseInfo | None, ancestors: list[AncestorLicense]) -> LicenseCheckResponse:
    """Apply the rule. Pure function: no database, easy to unit test."""
    inherited = None
    if own is None:
        inherited = next((a for a in ancestors if a.license), None)
    statuses = [license_status(a.license, a.commercial_use) for a in ancestors]
    if own is not None:
        own_status = license_status(own.id, own.commercial_use)
        verdict = worst([own_status] + statuses)
    elif inherited is not None:
        # no own license: the model is judged on its lineage only
        verdict = worst(statuses)
    else:
        verdict = Verdict.NEEDS_REVIEW

    conflicts = []
    if own is not None:
        for anc in ancestors:
            status = license_status(anc.license, anc.commercial_use)
            if anc.license and SEVERITY.index(status) > SEVERITY.index(own_status):
                conflicts.append(
                    LicenseConflict(
                        ancestor=anc,
                        reason=f"declares {own.id} but descends from {anc.id} ({anc.license}: {status.value})",
                    )
                )
    incomplete = any(a.unlisted for a in ancestors)
    return LicenseCheckResponse(
        model_id=model_id,
        declared_license=own,
        verdict=verdict,
        explanation=explain(own, inherited, ancestors, verdict, conflicts),
        inherited_from=inherited.id if inherited else None,
        conflicts=conflicts,
        ancestors=ancestors,
        incomplete_lineage=incomplete,
    )


def explain(own, inherited, ancestors, verdict, conflicts) -> str:
    """One readable paragraph, built only from the facts above."""
    if own is not None:
        parts = [f"The model declares {own.id} ({own.family or 'unknown'} family)."]
    elif inherited is not None:
        parts = [
            f"The model declares no license; the closest licensed ancestor is {inherited.id} ({inherited.license})."
        ]
    else:
        parts = ["Neither the model nor any ancestor declares a license."]
    if ancestors:
        parts.append(f"{len(ancestors)} ancestor(s) checked.")
    else:
        parts.append("No ancestor: it is a root model.")
    if conflicts:
        parts.append(f"{len(conflicts)} ancestor(s) have a stricter license than the one declared.")
    missing = sum(1 for a in ancestors if not a.license)
    if missing:
        parts.append(f"{missing} ancestor(s) declare no license, which needs a human check.")
    if any(a.unlisted for a in ancestors):
        parts.append("Part of the lineage is not in the public dump, so it can't be fully checked.")
    parts.append(f"Verdict: {verdict.value}.")
    return " ".join(parts)


class LicenseService:
    """Wraps evaluate() with the database read."""

    def __init__(self, repo: GraphRepository, settings: Settings):
        self.repo = repo
        self.settings = settings

    def check(self, model_id: str, depth: int | None = None) -> LicenseCheckResponse:
        """License check for one model, ancestors up to `depth` generations."""
        depth = check_depth(depth or self.settings.max_traversal_depth, self.settings)
        row = self.repo.license_check(model_id, depth)
        if row is None:
            raise NotFoundError(f"model '{model_id}' is not in the graph")
        own = LicenseInfo(**row["license"]) if row["license"] else None
        return evaluate(model_id, own, build_ancestors(row["ancestors"]))
