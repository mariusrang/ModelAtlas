"""Health endpoint, used by Docker healthchecks, CI and the UI status badge."""

from fastapi import APIRouter, Depends

from app import __version__
from app.database.neo4j_client import Neo4jClient
from app.dependencies import get_db
from app.models.health import DependencyStatus, HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Service and dependency status")
def health(db: Neo4jClient = Depends(get_db)) -> HealthResponse:
    """Return `ok` when every dependency answers, `degraded` otherwise.

    We always answer HTTP 200 so the container stays up and can recover on its
    own when Neo4j comes back; the status field tells the real story.
    """
    neo4j_ok = db.ping()
    deps = [DependencyStatus(name="neo4j", healthy=neo4j_ok, detail=None if neo4j_ok else "not reachable")]
    status = "ok" if all(d.healthy for d in deps) else "degraded"
    return HealthResponse(status=status, version=__version__, dependencies=deps)
