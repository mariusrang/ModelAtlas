"""Every Streamlit page, run with Streamlit's AppTest on a fake API (no network)."""

from pathlib import Path
from unittest.mock import patch

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from ui.api_client import ApiError

UI = Path(__file__).resolve().parents[2] / "ui"
MODEL = "TheBloke/Mistral-7B-Instruct-v0.2-GGUF"
BASE = "mistralai/Mistral-7B-Instruct-v0.2"
ROOT = "mistralai/Mistral-7B-v0.1"


def summary(model_id, downloads=1000, license_id="apache-2.0"):
    return {"id": model_id, "downloads": downloads, "likes": 10, "task": "text-generation", "license": license_id}


APACHE = {"id": "apache-2.0", "family": "permissive", "commercial_use": True, "url": None, "notes": None}
RESPONSES = {
    "/health": {"status": "ok", "version": "0.1.0", "dependencies": [{"name": "neo4j", "healthy": True}]},
    "/stats": {
        "models": 156071,
        "unlisted": 6138,
        "organizations": 43531,
        "licenses": 78,
        "datasets": 15495,
        "papers": 9420,
        "lineage_edges": {"finetune": 36907, "adapter": 10614, "quantized": 47560, "merge": 6700},
    },
    "/graph/influential": {
        "results": [{"model": summary(ROOT), "pagerank": 12.3, "descendant_count": 900, "family_id": 1}]
    },
    "/models/search": {"query": "mistral", "hits": [{"model": summary(MODEL), "rank": 3.0}]},
    f"/models/{MODEL}": {
        "model": summary(MODEL, 50000),
        "organization": "TheBloke",
        "license_info": APACHE,
        "created_at": "2023-12-11T00:00:00Z",
        "formats": ["gguf"],
        "datasets": [],
        "papers": [],
        "languages": [],
        "architectures": ["MistralForCausalLM"],
        "parents": [{"id": BASE, "relation": "quantized", "declared": True, "unlisted": False}],
        "lineage_depth": 2,
        "child_count": 0,
        "descendant_count": 0,
        "root_ids": [ROOT],
    },
    f"/models/{MODEL}/lineage": {
        "model_id": MODEL,
        "depth_limit": 4,
        "paths": [
            {
                "nodes": [{"id": MODEL, "downloads": 50000}, {"id": BASE, "downloads": 900000}, {"id": ROOT}],
                "relations": ["quantized", "finetune"],
                "complete": True,
            }
        ],
        "roots": [ROOT],
        "truncated": False,
    },
    f"/models/{MODEL}/descendants": {"model_id": MODEL, "items": [], "next_cursor": None},
    f"/models/{MODEL}/family": {
        "model_id": MODEL,
        "pagerank": 0.15,
        "family_id": 1,
        "family_size": 900,
        "rank_in_family": 40,
        "community": 3,
        "community_size": 120,
        "top_members": [{"id": BASE, "downloads": 900000, "pagerank": 5.0}],
    },
    f"/models/{MODEL}/similar": {"model_id": MODEL, "results": [], "note": "No similar model."},
    f"/models/{MODEL}/predicted-parents": {
        "model_id": MODEL,
        "declared_parents": 1,
        "results": [],
        "note": "This model declares its parent.",
    },
    f"/models/{MODEL}/license-check": {
        "model_id": MODEL,
        "declared_license": APACHE,
        "verdict": "commercial_use_ok",
        "explanation": "The model declares apache-2.0 (permissive family). 2 ancestor(s) checked. Verdict: ok.",
        "conflicts": [],
        "ancestors": [
            {
                "id": BASE,
                "distance": 1,
                "path": [MODEL, BASE],
                "relations": ["quantized"],
                "license": "apache-2.0",
                "commercial_use": True,
            },
            {
                "id": ROOT,
                "distance": 2,
                "path": [MODEL, BASE, ROOT],
                "relations": ["quantized", "finetune"],
                "license": "apache-2.0",
                "commercial_use": True,
            },
        ],
        "training_data": [
            {
                "id": "ds/nc",
                "used_by": BASE,
                "distance": 1,
                "listed": True,
                "license": "cc-by-nc-4.0",
                "commercial_use": False,
            }
        ],
        "depth_limit": 6,
        "incomplete_lineage": False,
        "disclaimer": "Informational only, not legal advice.",
    },
    "/connection": {
        "model_a": "HuggingFaceH4/zephyr-7b-beta",
        "model_b": BASE,
        "common_ancestor": {"id": ROOT, "downloads": 1, "distance_from_a": 1, "distance_from_b": 1},
        "shortest_path": {
            "nodes": [{"id": "HuggingFaceH4/zephyr-7b-beta"}, {"id": ROOT}, {"id": BASE}],
            "relations": ["finetune", "finetune"],
            "complete": True,
        },
        "shared_datasets": ["HuggingFaceH4/ultrachat_200k"],
        "shared_papers": [],
        "same_organization": False,
        "summary": "Both derive from mistralai/Mistral-7B-v0.1.",
    },
}
ANSWER = {
    "question": "q",
    "status": "answered",
    "answer": "Yes, it is Apache-2.0.",
    "answer_evidence": "HYPOTHESIS",
    "confidence": 0.95,
    "confidence_reasons": ["the answer cites 1 fact(s) returned by the tools"],
    "refusal_reason": None,
    "facts": [
        {
            "id": 1,
            "statement": "verdict: commercial_use_ok",
            "evidence": "DERIVED",
            "source": "license_check",
            "cited": True,
        }
    ],
    "steps": [{"tool": "license_check", "arguments": {"model_id": MODEL}, "success": True, "facts": 1}],
    "cypher": [
        {"cypher": "MATCH (m:Model) RETURN m.id LIMIT 25", "stage": "ok", "errors": [], "fixes": ["added LIMIT 25"]}
    ],
    "warnings": [],
    "model": "qwen2.5:7b-instruct",
    "elapsed_ms": 12000,
}
RECOMMEND = {
    "brief": {},
    "considered": 42,
    "results": [{"model": summary(BASE), "score": 7.2, "license_verdict": "commercial_use_ok", "reasons": ["popular"]}],
}


def fake_get(self, path, **params):  # pylint: disable=unused-argument
    if path not in RESPONSES:
        raise ApiError(404, f"{path} not found")
    return RESPONSES[path]


@pytest.fixture(autouse=True)
def fake_api():
    st.cache_data.clear()
    with (
        patch("ui.api_client.ApiClient.get", fake_get),
        patch("ui.api_client.ApiClient.post", lambda self, path, payload, timeout=30: RECOMMEND),
        patch("ui.api_client.ApiClient.ask", lambda self, question: ANSWER),
    ):
        yield


def run(page: str, **query) -> AppTest:
    app = AppTest.from_file(str(UI / "views" / page), default_timeout=30)
    for key, value in query.items():
        app.query_params[key] = value
    return app.run()


def test_overview():
    app = run("overview.py")
    assert not app.exception
    assert app.title[0].value == "ModelAtlas"
    assert app.metric[0].value == "156.1k"
    assert len(app.dataframe) == 1


def test_model_explorer_from_a_link():
    app = run("model.py", id=MODEL)
    assert not app.exception
    assert app.subheader[0].value == MODEL
    assert [t.label for t in app.tabs] == [
        "Card facts",
        "Family tree",
        "Derived models",
        "Graph analytics",
        "Predicted parent",
    ]
    assert app.metric[0].value == "50.0k"


def test_model_explorer_search_and_unknown_model():
    app = run("model.py", id="nobody/nothing")
    assert not app.exception
    assert "Not found" in app.warning[0].value
    app = run("model.py", id="mistral")  # free text goes through the search
    assert app.selectbox[0].value == MODEL


def test_license_check():
    app = run("license.py", id=MODEL)
    assert not app.exception
    assert any("Commercial use looks OK" in m.value for m in app.markdown)
    assert [m.value for m in app.metric][:4] == ["2", "0", "1", "1"]


def test_finder():
    app = run("finder.py")
    assert not app.exception
    app.button[0].click().run()  # the form's submit button
    assert not app.exception
    assert app.metric[0].value == "7.20"
    assert "42 models matched" in app.caption[0].value


def test_compare():
    app = run("compare.py")
    assert not app.exception
    assert app.metric[0].value == "Mistral-7B-v0.1"


def test_agent_chat_shows_answer_facts_and_trail():
    app = run("agent.py")
    assert not app.exception
    app.chat_input[0].set_value("Can I use it commercially?").run()
    assert not app.exception
    texts = " ".join(m.value for m in app.markdown)
    assert "Yes, it is Apache-2.0." in texts
    assert "license_check" in texts
    assert len(app.expander) == 2
    assert app.code[0].value.startswith("MATCH")


def test_agent_shows_api_errors():
    with patch("ui.api_client.ApiClient.ask", side_effect=ApiError(503, "the local LLM is not reachable")):
        app = run("agent.py")
        app.chat_input[0].set_value("hello there").run()
    assert not app.exception
    assert "not reachable" in app.info[0].value
