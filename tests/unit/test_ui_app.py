"""Smoke tests for the Streamlit home page, using Streamlit's AppTest runner."""

from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

APP_FILE = str(Path(__file__).resolve().parents[2] / "ui" / "app.py")
HEALTHY = {"status": "ok", "version": "0.1.0", "dependencies": [{"name": "neo4j", "healthy": True, "detail": None}]}


def test_home_page_shows_status_when_api_is_up():
    with patch("ui.api_client.ApiClient.health", return_value=HEALTHY):
        app = AppTest.from_file(APP_FILE).run()
    assert not app.exception
    assert app.title[0].value == "ModelAtlas"
    assert [m.value for m in app.metric] == ["OK", "up"]


def test_home_page_shows_error_when_api_is_down():
    with patch("ui.api_client.ApiClient.health", return_value=None):
        app = AppTest.from_file(APP_FILE).run()
    assert not app.exception
    assert "not reachable" in app.error[0].value
