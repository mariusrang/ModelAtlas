"""FastAPI dependencies shared by the routers (DB client, settings, auth)."""

import secrets

from fastapi import Depends, Header, Request

from app.config import Settings, get_settings
from app.database.neo4j_client import Neo4jClient
from app.errors import UnauthorizedError
from app.repositories.graph_repository import GraphRepository


def get_db(request: Request) -> Neo4jClient:
    """Return the Neo4j client created at startup (see `main.lifespan`)."""
    return request.app.state.db


def get_app_settings(request: Request, default: Settings = Depends(get_settings)) -> Settings:
    """Settings the app was built with (`create_app(settings=...)`), else the global ones."""
    return getattr(request.app.state, "settings", None) or default


def get_repository(db: Neo4jClient = Depends(get_db)) -> GraphRepository:
    """One repository per request; it only holds a reference to the shared client."""
    return GraphRepository(db)


def require_admin_key(
    x_api_key: str | None = Header(default=None, description="Admin API key"),
    settings: Settings = Depends(get_app_settings),
) -> None:
    """Protect sensitive endpoints (re-ingest, retrain...) with an API key.

    `secrets.compare_digest` compares in constant time, so the response time
    does not leak how many characters of the key were right.
    """
    expected = settings.admin_api_key.get_secret_value()
    if expected.startswith("change-me"):
        # the placeholder from .env.example is public, so it must never open anything
        raise UnauthorizedError("Admin endpoints are disabled until ADMIN_API_KEY is set in .env")
    if not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise UnauthorizedError("Missing or invalid X-API-Key header")
