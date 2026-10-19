"""Endpoints backed by the GDS results (PageRank, WCC, Louvain, Node Similarity)."""

from app.errors import NotFoundError, ServiceUnavailableError
from app.models.analytics import (
    FamilyMember,
    FamilyResponse,
    InfluentialResponse,
    PredictedParent,
    PredictedParentsResponse,
    RankedModel,
    SimilarModel,
    SimilarResponse,
)
from app.repositories.graph_repository import GraphRepository
from app.services.common import to_summary

NOT_COMPUTED = "graph algorithms have not been run on this graph yet: run `make gds`"
NOT_PREDICTED = "parent predictions have not been computed on this graph yet: run `make predict`"


class AnalyticsService:
    """Read the results `make gds` wrote on the graph."""

    def __init__(self, repo: GraphRepository):
        self.repo = repo

    def influential(self, task: str | None, limit: int) -> InfluentialResponse:
        """Top models by PageRank."""
        rows = self.repo.influential(task, limit)
        if not rows and not self.repo.gds_ready():
            raise ServiceUnavailableError(NOT_COMPUTED)
        return InfluentialResponse(
            task=task,
            results=[
                RankedModel(
                    model=to_summary(r["model"]),
                    pagerank=round(r["pagerank"], 4),
                    descendant_count=r["descendant_count"],
                    family_id=r["family_id"],
                )
                for r in rows
            ],
        )

    def similar(self, model_id: str, limit: int) -> SimilarResponse:
        """Models sharing datasets / papers with this one."""
        row = self.repo.similar_models(model_id, limit)
        if row is None:
            raise NotFoundError(f"model '{model_id}' is not in the graph")
        if not row["gds_ready"]:
            raise ServiceUnavailableError(NOT_COMPUTED)
        results = [
            SimilarModel(
                model=to_summary(s["model"]),
                score=round(s["score"], 3),
                shared_datasets=s["shared_datasets"],
                shared_papers=s["shared_papers"],
            )
            for s in row["similar"]
        ]
        note = None
        if not results:
            note = "No similar model: it declares fewer than 2 datasets/papers, or none of them is shared enough."
        return SimilarResponse(model_id=model_id, results=results, note=note)

    def family(self, model_id: str, limit: int) -> FamilyResponse:
        """Family (WCC), community (Louvain) and rank (PageRank)."""
        row = self.repo.model_family(model_id, limit)
        if row is None:
            raise NotFoundError(f"model '{model_id}' is not in the graph")
        if row["pagerank"] is None or row["family_id"] is None or row["community"] is None:
            raise ServiceUnavailableError(NOT_COMPUTED)
        return FamilyResponse(
            model_id=model_id,
            pagerank=round(row["pagerank"], 4),
            family_id=row["family_id"],
            family_size=row["family_size"],
            rank_in_family=row["rank_in_family"],
            community=row["community"],
            community_size=row["community_size"],
            top_members=[FamilyMember(**m) for m in row["top_members"]],
        )

    def predicted_parents(self, model_id: str, limit: int) -> PredictedParentsResponse:
        """Parents guessed by `make predict` (HYPOTHESIS)."""
        row = self.repo.predicted_parents(model_id, limit)
        if row is None:
            raise NotFoundError(f"model '{model_id}' is not in the graph")
        if not row["predictions_ready"]:
            raise ServiceUnavailableError(NOT_PREDICTED)
        results = [
            PredictedParent(
                model=to_summary(p["model"]),
                score=p["score"],
                rank=p["rank"],
                name_score=p["name_score"],
                content_cosine=p["content_cosine"],
            )
            for p in row["predictions"]
        ]
        note = None
        if row["declared_parents"]:
            note = "This model declares its parent in its card: see /lineage, predictions are only made for orphans."
        elif not results:
            note = (
                "No prediction: the model has under 100 downloads, or no candidate scored above the threshold "
                "chosen for 80% precision."
            )
        version = row["predictions"][0]["model_version"] if row["predictions"] else None
        return PredictedParentsResponse(
            model_id=model_id,
            model_version=version,
            declared_parents=row["declared_parents"],
            results=results,
            note=note,
        )
