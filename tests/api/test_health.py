"""API tests for /health."""

from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import FakeNeo4jClient


def test_health_ok_when_neo4j_answers(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["dependencies"][0] == {"name": "neo4j", "healthy": True, "detail": None}


def test_health_degraded_when_neo4j_down(settings):
    app = create_app(settings=settings, db=FakeNeo4jClient(alive=False))
    with TestClient(app) as client:
        body = client.get("/health").json()
    assert body["status"] == "degraded"
    assert body["dependencies"][0]["detail"] == "not reachable"


def test_every_response_has_request_id_and_timing(client):
    response = client.get("/health", headers={"X-Request-ID": "abc123"})
    assert response.headers["X-Request-ID"] == "abc123"
    assert float(response.headers["X-Response-Time-ms"]) >= 0


def test_db_is_closed_on_shutdown(settings):
    fake = FakeNeo4jClient()
    with TestClient(create_app(settings=settings, db=fake)):
        pass
    assert fake.closed


def test_openapi_docs_are_served(client):
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json").json()
    assert schema["info"]["title"] == "ModelAtlas API"
