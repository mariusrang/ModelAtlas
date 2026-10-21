"""Unit tests for the error handlers and the admin API-key dependency."""

import pytest
from fastapi import Depends, FastAPI, Query
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.dependencies import require_admin_key
from app.errors import (
    BadRequestError,
    NotFoundError,
    QueryTimeoutError,
    ServiceUnavailableError,
    UnauthorizedError,
    register_error_handlers,
)


@pytest.fixture(name="error_app")
def fixture_error_app() -> TestClient:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/boom/{kind}")
    def boom(kind: str):
        errors = {
            "404": NotFoundError("model not found"),
            "400": BadRequestError("depth too big"),
            "503": ServiceUnavailableError("neo4j down"),
            "504": QueryTimeoutError("query stopped after 10 s"),
        }
        raise errors[kind]

    @app.get("/validated")
    def validated(depth: int = Query(ge=1, le=6)):
        return {"depth": depth}

    @app.post("/admin", dependencies=[Depends(require_admin_key)])
    def admin():
        return {"done": True}

    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, admin_api_key="secret")
    return TestClient(app)


@pytest.mark.parametrize(
    ("kind", "status", "code"),
    [
        ("404", 404, "not_found"),
        ("400", 400, "bad_request"),
        ("503", 503, "service_unavailable"),
        ("504", 504, "query_timeout"),
    ],
)
def test_app_errors_map_to_http_codes(error_app, kind, status, code):
    response = error_app.get(f"/boom/{kind}")
    assert response.status_code == status
    body = response.json()
    assert body["code"] == code
    assert set(body) == {"code", "detail", "request_id"}


def test_validation_errors_use_same_shape(error_app):
    response = error_app.get("/validated", params={"depth": 99})
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_admin_requires_key(error_app):
    assert error_app.post("/admin").status_code == 401
    assert error_app.post("/admin", headers={"X-API-Key": "wrong"}).status_code == 401
    assert error_app.post("/admin", headers={"X-API-Key": "secret"}).json() == {"done": True}


def test_unauthorized_error_defaults():
    err = UnauthorizedError("nope")
    assert (err.status_code, err.code, err.detail) == (401, "unauthorized", "nope")
