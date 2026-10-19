"""Dataset pages: the second data source (dataset cards) joined to the model graph."""

from app.errors import NotFoundError
from app.models.catalog import LicenseInfo
from app.models.dataset import DatasetDetail
from app.repositories.graph_repository import GraphRepository
from app.services.common import to_summary


class DatasetService:
    """Read-only dataset features."""

    def __init__(self, repo: GraphRepository):
        self.repo = repo

    def detail(self, dataset_id: str, limit: int) -> DatasetDetail:
        """Dataset card facts and the most downloaded models trained on it."""
        row = self.repo.dataset_detail(dataset_id, limit)
        if row is None:
            raise NotFoundError(f"dataset '{dataset_id}' is not in the graph")
        data = row["dataset"]
        return DatasetDetail(
            id=data["id"],
            listed=bool(data.get("listed")),
            author=data.get("author"),
            downloads=data.get("downloads"),
            likes=data.get("likes"),
            created_at=data.get("created_at"),
            size_category=data.get("size_category"),
            task_categories=data.get("task_categories") or [],
            license_info=LicenseInfo(**row["license"]) if row["license"] else None,
            languages=row["languages"],
            model_count=row["model_count"],
            top_models=[to_summary(m) for m in row["top_models"]],
        )
