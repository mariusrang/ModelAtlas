"""Unit tests for the UI's API client."""

from unittest.mock import MagicMock

import requests

from ui.api_client import ApiClient


def test_health_returns_none_when_api_down():
    session = MagicMock()
    session.get.side_effect = requests.ConnectionError("down")
    assert ApiClient("http://api:8000/", session=session).health() is None


def test_get_builds_url_and_returns_json():
    session = MagicMock()
    session.get.return_value.json.return_value = {"status": "ok"}
    client = ApiClient("http://api:8000/", session=session)
    assert client.health() == {"status": "ok"}
    assert session.get.call_args.args[0] == "http://api:8000/health"
