"""Shortlist of models for a client brief ("text-generation, under 8B, French, commercial")."""

import math

from app.config import Settings
from app.models.license import Verdict
from app.models.recommend import Recommendation, RecommendRequest, RecommendResponse
from app.repositories.graph_repository import GraphRepository
from app.services.common import to_summary
from app.services.license import LicenseService

MAX_POOL = 60
ACCEPTED = (Verdict.OK, Verdict.CONDITIONAL)


def popularity_score(downloads: int | None, likes: int | None) -> float:
    """Downloads matter most; likes break ties. Logs so one giant model doesn't crush the scale."""
    return round(math.log10((downloads or 0) + 1) + 0.5 * math.log10((likes or 0) + 1), 3)


def reasons_for(row: dict, brief: RecommendRequest, verdict: Verdict | None) -> list[str]:
    """Human-readable reasons, each one backed by a field of the row."""
    model = row["model"]
    reasons = [f"task is {model['task']}", f"{model['downloads'] or 0:,} downloads, {model['likes'] or 0} likes"]
    if brief.max_params_b is not None and model.get("params_b") is not None:
        reasons.append(f"about {model['params_b']}B parameters (limit {brief.max_params_b}B)")
    if brief.language:
        reasons.append(f"declares support for '{brief.language}'")
    if verdict is not None:
        reasons.append(f"license check on the lineage and training data: {verdict.value}")
    if model.get("descendant_count"):
        reasons.append(f"{model['descendant_count']} models derived from it (well-adopted base)")
    return reasons


class RecommendService:
    """Filter in Cypher, rank and license-check in Python."""

    def __init__(self, repo: GraphRepository, settings: Settings):
        self.repo = repo
        self.licenses = LicenseService(repo, settings)

    def recommend(self, brief: RecommendRequest) -> RecommendResponse:
        """Top `brief.limit` models matching the brief."""
        rows = self.repo.recommend_candidates(
            {
                "task": brief.task,
                "max_params": brief.max_params_b,
                "min_downloads": brief.min_downloads,
                "language": brief.language,
                "commercial": brief.commercial_use,
                "pool": min(brief.limit * 3, MAX_POOL),
            }
        )
        rows.sort(key=lambda r: popularity_score(r["model"]["downloads"], r["model"]["likes"]), reverse=True)
        # The candidate's own license allows it (filtered in Cypher); its ancestors
        # and training data must too. One batched query for the whole pool.
        checks = self.licenses.check_many([r["model"]["id"] for r in rows]) if brief.commercial_use else {}
        results = []
        for row in rows:
            verdict = None
            if brief.commercial_use:
                check = checks.get(row["model"]["id"])
                verdict = check.verdict if check else Verdict.NEEDS_REVIEW
                if verdict not in ACCEPTED:
                    continue
            results.append(
                Recommendation(
                    model=to_summary(row["model"]),
                    score=popularity_score(row["model"]["downloads"], row["model"]["likes"]),
                    license_verdict=verdict,
                    reasons=reasons_for(row, brief, verdict),
                )
            )
            if len(results) == brief.limit:
                break
        return RecommendResponse(brief=brief.model_dump(), results=results, considered=len(rows))
