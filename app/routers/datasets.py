"""Dataset endpoints (data from the dataset cards, our second source)."""

from fastapi import APIRouter, Depends, Query

from app.dependencies import get_repository
from app.models.dataset import DatasetDetail
from app.repositories.graph_repository import GraphRepository
from app.services.dataset import DatasetService

router = APIRouter(prefix="/datasets", tags=["datasets"])


@router.get("/{dataset_id:path}", response_model=DatasetDetail, summary="Dataset page and models trained on it")
def dataset_detail(
    dataset_id: str,
    limit: int = Query(default=10, ge=1, le=50, description="How many models to list"),
    repo: GraphRepository = Depends(get_repository),
) -> DatasetDetail:
    """License and languages from the dataset card, most downloaded models that declare it."""
    return DatasetService(repo).detail(dataset_id, limit)
