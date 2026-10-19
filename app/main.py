"""FastAPI application factory.

Run locally with `make run`, or through docker compose with `make docker-run`.
Interactive docs: http://localhost:8000/docs (or http://localhost/api/docs behind Nginx).
"""

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from app import __version__
from app.config import Settings, get_settings
from app.database.neo4j_client import Neo4jClient
from app.errors import register_error_handlers
from app.routers import admin, datasets, graph, health, models

logger = logging.getLogger("modelatlas")

DESCRIPTION = """
**ModelAtlas** is a knowledge graph of the open AI model ecosystem (Hugging Face models,
their family trees, licenses, datasets, papers and benchmarks).

Every fact returned by the API carries an evidence label:
`VERIFIED` (declared in a model card), `DERIVED` (our rule applied to verified data)
or `HYPOTHESIS` (ML or LLM inference, always with a confidence score).
"""


def create_app(settings: Settings | None = None, db: Neo4jClient | None = None) -> FastAPI:
    """Build the FastAPI app.

    `settings` and `db` can be injected, which is what the tests do to run the
    API without a real Neo4j.
    """
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.db = db or Neo4jClient(settings)
        yield
        application.state.db.close()

    application = FastAPI(
        title="ModelAtlas API",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
    )

    @application.middleware("http")
    async def add_request_id_and_timing(request: Request, call_next):
        # A request id in every response and log line makes debugging much easier.
        request.state.request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex[:12])
        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Response-Time-ms"] = f"{elapsed_ms:.1f}"
        logger.info(f"{request.method} {request.url.path} -> {response.status_code} ({elapsed_ms:.1f} ms)")
        return response

    application.state.settings = settings
    register_error_handlers(application)
    application.include_router(health.router)
    application.include_router(models.router)
    application.include_router(graph.router)
    application.include_router(datasets.router)
    application.include_router(admin.router)
    return application


app = create_app()
