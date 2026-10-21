"""Shared pytest fixtures.

Unit and API tests run without Neo4j: we inject a fake client into the app.
Tests marked `integration` need a real Neo4j and are run by `make test-integration`.
"""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


class FakeNeo4jClient:
    """Stand-in for Neo4jClient: returns canned rows and records the queries."""

    def __init__(self, rows=None, alive: bool = True):
        self.rows = rows or []
        self.alive = alive
        self.calls: list[tuple[str, dict]] = []
        self.closed = False

    def ping(self) -> bool:
        return self.alive

    def read(self, cypher: str, params=None):
        self.calls.append((cypher, params or {}))
        return self.rows

    def write(self, cypher: str, params=None):
        self.calls.append((cypher, params or {}))
        return self.rows

    def explain(self, cypher: str, params=None):
        self.calls.append((f"EXPLAIN {cypher}", params or {}))
        return {"operators": ["ProduceResults"], "estimated_rows": 1.0}

    def close(self) -> None:
        self.closed = True


@pytest.fixture(name="settings")
def fixture_settings() -> Settings:
    """Settings with known test values (no `.env` needed)."""
    return Settings(_env_file=None, admin_api_key="test-key", neo4j_password="test-password")


@pytest.fixture(name="fake_db")
def fixture_fake_db() -> FakeNeo4jClient:
    return FakeNeo4jClient()


@pytest.fixture(name="client")
def fixture_client(settings, fake_db):
    """TestClient on an app wired to the fake database."""
    app = create_app(settings=settings, db=fake_db)
    with TestClient(app) as test_client:
        yield test_client
