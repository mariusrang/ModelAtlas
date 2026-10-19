"""Agent evaluation script: case file, grading and report (no LLM needed)."""

import pytest

from scripts import evaluate_agent as ev


def test_the_case_file_is_valid():
    cases = ev.load_cases()
    assert len(cases) >= 8
    assert {c.kind for c in cases} == {"normal", "trap"}
    assert {c.expect for c in cases} <= set(ev.STATUS_FOR)


def test_bad_case_files_are_rejected(tmp_path):
    path = tmp_path / "cases.toml"
    path.write_text('[[case]]\nid = "a"\nquestion = "q"\nexpect = "maybe"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="expect"):
        ev.load_cases(path)
    path.write_text('[[case]]\nid = "a"\nquestion = "q"\nexpect = "answer"\n' * 2, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        ev.load_cases(path)


def _result(**overrides):
    result = {
        "status": "answered",
        "answer": "It is under Apache 2.0.",
        "steps": [{"tool": "license_check", "success": True}],
        "facts": [{"id": 1, "statement": "verdict: commercial_use_ok"}],
        "fact_ids": [1],
        "warnings": [],
        "cypher": [],
        "confidence": 0.9,
        "elapsed_ms": 1200,
    }
    return {**result, **overrides}


def test_grade():
    case = ev.Case(id="x", question="q", expect="answer", tools=["license_check"], mentions=["apache", "commercial"])
    assert not ev.grade(case, _result())
    problems = ev.grade(case, _result(status="refused", steps=[], answer="no", fact_ids=[]))
    assert len(problems) == 4  # wrong status, no tool, two missing words
    trap = ev.Case(id="t", question="q", expect="refuse", kind="trap")
    assert not ev.grade(trap, _result(status="refused"))


def test_report():
    cases = [
        ev.Case(id="ok", question="q1", expect="answer"),
        ev.Case(id="trap", question="q2", expect="refuse", kind="trap"),
    ]
    results = [
        {**_result(cypher=[{"cypher": "MATCH (m) RETURN m", "stage": "ok", "errors": []}]), "problems": []},
        {**_result(status="answered"), "problems": ["status answered, expected refused"]},
    ]
    report = ev.render_report(cases, results, "test-llm")
    assert "- passed: **1**" in report
    assert "- traps handled: **0.00**" in report
    assert "FAIL: status answered" in report
    assert "Cypher (ok)" in report
