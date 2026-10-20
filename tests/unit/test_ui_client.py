"""Unit tests for the UI's API client."""

from unittest.mock import MagicMock

import pytest
import requests

from ui.api_client import ApiClient, ApiError, model_path


def test_health_returns_none_when_api_down():
    session = MagicMock()
    session.request.side_effect = requests.ConnectionError("down")
    assert ApiClient("http://api:8000/", session=session).health() is None


def test_get_builds_url_drops_none_and_returns_json():
    session = MagicMock()
    session.request.return_value.json.return_value = {"status": "ok"}
    client = ApiClient("http://api:8000/", session=session)
    assert client.get("/health", task=None, limit=5) == {"status": "ok"}
    method, url = session.request.call_args.args
    assert (method, url) == ("GET", "http://api:8000/health")
    assert session.request.call_args.kwargs["params"] == {"limit": 5}


def test_errors_carry_the_api_detail():
    session = MagicMock()
    session.request.return_value.ok = False
    session.request.return_value.status_code = 404
    session.request.return_value.json.return_value = {"code": "not_found", "detail": "model 'x' is not in the graph"}
    with pytest.raises(ApiError) as error:
        ApiClient(session=session).get("/models/x")
    assert error.value.not_found and "not in the graph" in error.value.detail


def test_validation_errors_are_joined():
    session = MagicMock()
    session.request.return_value.ok = False
    session.request.return_value.status_code = 422
    session.request.return_value.json.return_value = {"detail": [{"msg": "too short"}, {"msg": "bad"}]}
    with pytest.raises(ApiError, match="too short; bad"):
        ApiClient(session=session).post("/agent/ask", {"question": "x"})


def test_ask_uses_a_long_timeout():
    session = MagicMock()
    session.request.return_value.json.return_value = {"status": "answered"}
    ApiClient(session=session).ask("hello there")
    assert session.request.call_args.kwargs["timeout"] >= 120
    assert session.request.call_args.kwargs["json"] == {"question": "hello there"}


def test_model_path():
    assert model_path(" org/My Model ") == "org/My%20Model"
    assert ApiError(503, "x").unavailable and ApiError(None, "x").unavailable
