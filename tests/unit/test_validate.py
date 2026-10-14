"""Unit tests for post-load validation and the seed orchestrator."""

import json

import pytest

from app.database.schema import CONSTRAINTS
from scripts import seed_data
from scripts.etl import validate as validate_module
from scripts.etl.validate import render_report, validate

ROW_COUNTS = {"models": 10, "unlisted": 1, "lineage": 8}
CONSTRAINT_NAMES = [s.split()[2] for s in CONSTRAINTS]


class ScriptedClient:
    """Fake Neo4j client that answers each validation query with canned rows."""

    def __init__(self, models=11, lineage=8, self_loops=0, constraints=None):
        self.answers = {
            "db.labels()": [{"label": "Model", "nodes": models}, {"label": "Unlisted", "nodes": 1}],
            "db.relationshipTypes()": [
                {"type": "FINETUNED_FROM", "rels": lineage - 1},
                {"type": "MERGED_FROM", "rels": 1},
                {"type": "PUBLISHED_BY", "rels": 11},
            ],
            "SHOW CONSTRAINTS": [{"names": CONSTRAINT_NAMES if constraints is None else constraints}],
            "-[r:FINETUNED_FROM": [{"n": self_loops}],
        }
        self.closed = False

    def read(self, query, params=None):  # pylint: disable=unused-argument
        for needle, rows in self.answers.items():
            if needle in query:
                return rows
        return [{"n": 0}]

    def write(self, query, params=None):  # pylint: disable=unused-argument
        return []

    def close(self):
        self.closed = True


def test_valid_graph_passes():
    result = validate(ScriptedClient(), ROW_COUNTS)
    assert result.passed, result.failures
    assert result.labels["Model"] == 11


@pytest.mark.parametrize(
    ("client", "message"),
    [
        (ScriptedClient(models=9), "Model nodes"),
        (ScriptedClient(lineage=5), "lineage relationships"),
        (ScriptedClient(self_loops=2), "self-loops"),
        (ScriptedClient(constraints=["model_id"]), "missing constraints"),
    ],
)
def test_broken_graph_fails(client, message):
    result = validate(client, ROW_COUNTS)
    assert not result.passed
    assert any(message in failure for failure in result.failures)


def test_report_lists_counts_and_status():
    report = render_report(validate(ScriptedClient(), ROW_COUNTS), {"models": 1.5})
    assert "all checks passed" in report
    assert "| FINETUNED_FROM | 7 |" in report and "| models | 1.5 |" in report
    assert validate_module.LINEAGE.startswith("FINETUNED_FROM|")


def test_seed_main_runs_load_and_validation(tmp_path, monkeypatch):
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "manifest.json").write_text(json.dumps({"row_counts": ROW_COUNTS}), encoding="utf-8")
    client = ScriptedClient()
    monkeypatch.setattr(seed_data.transform, "STAGED_DIR", staged)
    monkeypatch.setattr(seed_data, "REPORTS_DIR", tmp_path)
    monkeypatch.setattr(seed_data, "build_client", lambda uri: client)
    monkeypatch.setattr(seed_data.GraphLoader, "load_all", lambda self, staged_dir: {"models": 0.1})

    assert seed_data.main(["--skip-transform"]) == 0
    assert client.closed
    assert "all checks passed" in (tmp_path / "load_report.md").read_text(encoding="utf-8")
