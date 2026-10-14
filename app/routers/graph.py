"""Graph-wide endpoints: stats, connection between two models, recommendation, organizations."""

from fastapi import APIRouter, Depends, Query

from app.config import Settings
from app.dependencies import get_app_settings, get_repository
from app.models.catalog import StatsResponse
from app.models.lineage import ConnectionResponse
from app.models.organization import FootprintResponse
from app.models.recommend import RecommendRequest, RecommendResponse
from app.repositories.graph_repository import GraphRepository
from app.services.catalog import CatalogService
from app.services.lineage import LineageService
from app.services.organization import OrganizationService
from app.services.recommend import RecommendService

router = APIRouter(tags=["graph"])


@router.get("/stats", response_model=StatsResponse, summary="Graph size")
def stats(repo: GraphRepository = Depends(get_repository)) -> StatsResponse:
    """Node and lineage-edge counts."""
    return CatalogService(repo).stats()


@router.get("/connection", response_model=ConnectionResponse, summary="How are two models related?")
def connection(
    a: str = Query(min_length=3, examples=["mistralai/Mistral-7B-v0.1"]),
    b: str = Query(min_length=3, examples=["HuggingFaceH4/zephyr-7b-beta"]),
    depth: int = Query(default=4, ge=1, le=12),
    repo: GraphRepository = Depends(get_repository),
    settings: Settings = Depends(get_app_settings),
) -> ConnectionResponse:
    """Closest common ancestor, shortest lineage path, shared datasets and papers."""
    return LineageService(repo, settings).connection(a, b, depth)


@router.post("/recommend", response_model=RecommendResponse, summary="Shortlist for a client brief")
def recommend(
    brief: RecommendRequest,
    repo: GraphRepository = Depends(get_repository),
    settings: Settings = Depends(get_app_settings),
) -> RecommendResponse:
    """Filter by task / size / language / license, rank by adoption, explain each pick."""
    return RecommendService(repo, settings).recommend(brief)


@router.get(
    "/organizations/{org_id}/footprint",
    response_model=FootprintResponse,
    summary="Who builds on this organization's models?",
)
def organization_footprint(
    org_id: str,
    depth: int = Query(default=3, ge=1, le=12),
    limit: int = Query(default=10, ge=1, le=50),
    repo: GraphRepository = Depends(get_repository),
    settings: Settings = Depends(get_app_settings),
) -> FootprintResponse:
    """Own models, derived models and the top builders."""
    return OrganizationService(repo, settings).footprint(org_id, depth, limit)
