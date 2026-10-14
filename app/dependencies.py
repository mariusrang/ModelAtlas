"""FastAPI dependencies shared by the routers (DB client, settings, auth)."""

import secrets

from fastapi import Depends, Header, Request

from app.config import Settings, get_settings
from app.database.neo4j_client import Neo4jClient
from app.errors import UnauthorizedError


def get_db(request: Request) -> Neo4jClient:
    """Return the Neo4j client created at startup (see `main.lifespan`)."""
    return request.app.state.db


def require_admin_key(
    x_api_key: str | None = Header(default=None, description="Admin API key"),
    settings: Settings = Depends(get_settings),
) -> None:
    """Protect sensitive endpoints (re-ingest, retrain...) with an API key.

    `secrets.compare_digest` compares in constant time, so the response time
    does not leak how many characters of the key were right.
    """
    expected = settings.admin_api_key.get_secret_value()
    if not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise UnauthorizedError("Missing or invalid X-API-Key header")
