"""The app shell (navigation + sidebar status) and the shared UI helpers."""

from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from ui import components as ui

APP_FILE = str(Path(__file__).resolve().parents[2] / "ui" / "app.py")
HEALTHY = {"status": "ok", "version": "0.1.0", "dependencies": [{"name": "neo4j", "healthy": True, "detail": None}]}


def test_app_runs_and_shows_api_status():
    with (
        patch("ui.api_client.ApiClient.health", return_value=HEALTHY),
        patch("ui.api_client.ApiClient.get", side_effect=ui.ApiError(None, "the API is not reachable")),
    ):
        app = AppTest.from_file(APP_FILE, default_timeout=30).run()
    assert not app.exception
    assert any("API 0.1.0" in c.value for c in app.sidebar.caption)


def test_app_says_when_the_api_is_down():
    with (
        patch("ui.api_client.ApiClient.health", return_value=None),
        patch("ui.api_client.ApiClient.get", side_effect=ui.ApiError(None, "the API is not reachable")),
    ):
        app = AppTest.from_file(APP_FILE, default_timeout=30).run()
    assert not app.exception
    assert "API not reachable" in app.sidebar.error[0].value


def test_human_numbers():
    assert [ui.human(v) for v in (None, 12, 1500, 2_500_000, 3e9, 0.5)] == ["-", "12", "1.5k", "2.5M", "3.0B", "0.5"]


def test_badges_escape_html():
    assert "ma-verified" in ui.evidence_badge("VERIFIED")
    assert "&lt;b&gt;" in ui.badge("<b>")


def test_lineage_dot_draws_every_edge_once_and_marks_cut_paths():
    paths = [
        {
            "nodes": [{"id": "a/child"}, {"id": "b/parent", "unlisted": True}],
            "relations": ["quantized"],
            "complete": False,
        },
        {"nodes": [{"id": "a/child"}, {"id": "b/parent"}], "relations": ["quantized"], "complete": True},
    ]
    dot = ui.lineage_dot("a/child", paths, {"b/parent": "#ffe3e3"})
    assert dot.count('"a/child" -> "b/parent"') == 1
    assert "dashed" in dot and "#ffe3e3" in dot
    assert "higher ancestors" in dot


def test_license_helpers_match_the_api_rule():
    assert ui.license_status(None, None) == "needs_review"
    assert ui.license_status("mit", True) == "commercial_use_ok"
    assert ui.license_status("cc-by-nc-4.0", False) == "not_allowed"
    assert ui.license_status("llama3", "conditional") == "conditional"
    paths = ui.license_paths("me/x", [{"id": "p/y", "path": ["me/x", "p/y"], "relations": ["finetune"]}])
    assert paths[0]["nodes"][1]["id"] == "p/y"


def test_connection_dot_without_path():
    dot = ui.connection_dot({"model_a": "a/x", "model_b": "b/y", "shortest_path": None, "common_ancestor": None})
    assert '"a/x"' in dot and '"b/y"' in dot


def test_model_url_keeps_the_slash():
    assert ui.model_url("org/name v2") == "/model?id=org/name%20v2"


def test_trail_dot_shows_tools_queries_and_refusals():
    result = {
        "status": "answered",
        "confidence": 0.75,
        "steps": [{"tool": "text_to_cypher", "success": True, "facts": 3}],
        "cypher": [{"stage": "guard"}, {"stage": "ok"}],
    }
    dot = ui.trail_dot(result)
    assert "1. text_to_cypher" in dot
    assert "rejected at\\nguard" in dot and "query ran" in dot
    assert "confidence 0.75" in dot
    refused = ui.trail_dot({"status": "refused"})
    assert "before any LLM call" in refused and "answer" not in refused
