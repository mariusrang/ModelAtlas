"""Model endpoints: search, model page, lineage, descendants, license check.

Model ids contain a "/" (mistralai/Mistral-7B-v0.1), hence the `:path`
converter. Routes with a suffix (/lineage...) are declared BEFORE the bare
/models/{model_id} route, otherwise "x/y/lineage" would be read as a model id.
"""

from fastapi import APIRouter, Depends, Query

from app.config import Settings
from app.dependencies import get_app_settings, get_repository
from app.domain import Relation
from app.models.catalog import ModelDetail, SearchResponse
from app.models.license import LicenseCheckResponse
from app.models.lineage import DescendantPage, LineageResponse
from app.repositories.graph_repository import GraphRepository
from app.services.catalog import CatalogService
from app.services.license import LicenseService
from app.services.lineage import LineageService

router = APIRouter(prefix="/models", tags=["models"])

DEPTH = Query(default=4, ge=1, le=12, description="Max generations to walk (capped by MAX_TRAVERSAL_DEPTH)")


@router.get("/search", response_model=SearchResponse, summary="Find models by name or author")
def search(
    q: str = Query(min_length=2, max_length=100, examples=["mistral 7b"]),
    task: str | None = Query(default=None, examples=["text-generation"]),
    limit: int = Query(default=10, ge=1, le=50),
    repo: GraphRepository = Depends(get_repository),
) -> SearchResponse:
    """Every word must match the start of a word in the id or author; popular models first."""
    return CatalogService(repo).search(q, task, limit)


@router.get("/{model_id:path}/lineage", response_model=LineageResponse, summary="Family tree up to the roots")
def lineage(
    model_id: str,
    depth: int = DEPTH,
    repo: GraphRepository = Depends(get_repository),
    settings: Settings = Depends(get_app_settings),
) -> LineageResponse:
    """All lineage paths from the model to its foundation model(s)."""
    return LineageService(repo, settings).lineage(model_id, depth)


@router.get("/{model_id:path}/descendants", response_model=DescendantPage, summary="Models derived from this one")
def descendants(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    model_id: str,
    depth: int = Query(default=2, ge=1, le=12),
    relation: Relation | None = Query(default=None, description="Only this kind of derivation"),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None, description="next_cursor from the previous page"),
    repo: GraphRepository = Depends(get_repository),
    settings: Settings = Depends(get_app_settings),
) -> DescendantPage:
    """Most downloaded first, keyset-paginated."""
    return LineageService(repo, settings).descendants(model_id, depth, relation, limit, cursor)


@router.get(
    "/{model_id:path}/license-check",
    response_model=LicenseCheckResponse,
    summary="Can a company use this model commercially?",
)
def license_check(
    model_id: str,
    depth: int | None = Query(default=None, ge=1, le=12, description="Default: MAX_TRAVERSAL_DEPTH"),
    repo: GraphRepository = Depends(get_repository),
    settings: Settings = Depends(get_app_settings),
) -> LicenseCheckResponse:
    """Walks every ancestor's license. Informational, not legal advice."""
    return LicenseService(repo, settings).check(model_id, depth)


@router.get("/{model_id:path}", response_model=ModelDetail, summary="Model page")
def model_detail(model_id: str, repo: GraphRepository = Depends(get_repository)) -> ModelDetail:
    """Card facts (VERIFIED) and precomputed lineage metrics (DERIVED)."""
    return CatalogService(repo).detail(model_id)
