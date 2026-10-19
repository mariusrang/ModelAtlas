"""The license verdict rule, tested without any database."""

import pytest

from app.models.license import Verdict
from app.services.license import LineageFacts, evaluate, facts_from_row, license_status, worst
from tests.fake_graph import APACHE, NON_COMMERCIAL, ancestor, dataset_row

LLAMA = {"id": "llama3.1", "family": "community", "commercial_use": "conditional", "url": None, "notes": None}


def facts(own=None, ancestors=(), datasets=(), lineage_depth=None) -> LineageFacts:
    """Build the facts the same way the service does, from Cypher-shaped rows."""
    if lineage_depth is None:
        lineage_depth = max((a["distance"] for a in ancestors), default=0)
    row = {"license": own, "ancestors": list(ancestors), "datasets": list(datasets), "lineage_depth": lineage_depth}
    return facts_from_row(row)


@pytest.mark.parametrize(
    ("license_id", "commercial_use", "expected"),
    [
        ("apache-2.0", True, Verdict.OK),
        ("llama3.1", "conditional", Verdict.CONDITIONAL),
        ("cc-by-nc-4.0", False, Verdict.NOT_ALLOWED),
        ("some-new-license", None, Verdict.NEEDS_REVIEW),
        (None, None, Verdict.NEEDS_REVIEW),
    ],
)
def test_license_status(license_id, commercial_use, expected):
    assert license_status(license_id, commercial_use) == expected


def test_worst_is_the_most_restrictive():
    assert worst([Verdict.OK, Verdict.NOT_ALLOWED, Verdict.CONDITIONAL]) == Verdict.NOT_ALLOWED
    assert worst([Verdict.OK, Verdict.CONDITIONAL]) == Verdict.CONDITIONAL
    assert worst([]) == Verdict.OK


def test_root_model_with_permissive_license():
    result = evaluate("acme/base", facts(APACHE), depth_limit=6)
    assert result.verdict == Verdict.OK
    assert "root model" in result.explanation
    assert result.incomplete_lineage is False


def test_finetune_cannot_remove_a_parent_restriction():
    result = evaluate(
        "me/ft", facts(APACHE, [ancestor("meta/llama", ["me/ft", "meta/llama"], ["FINETUNED_FROM"], LLAMA)]), 6
    )
    assert result.verdict == Verdict.CONDITIONAL
    assert len(result.conflicts) == 1
    assert "descends from meta/llama" in result.conflicts[0].reason


def test_non_commercial_ancestor_wins():
    rows = [
        ancestor("a/mid", ["me/x", "a/mid"], ["FINETUNED_FROM"], APACHE),
        ancestor("b/root", ["me/x", "a/mid", "b/root"], ["FINETUNED_FROM", "FINETUNED_FROM"], NON_COMMERCIAL),
    ]
    result = evaluate("me/x", facts(APACHE, rows), 6)
    assert result.verdict == Verdict.NOT_ALLOWED
    assert [c.ancestor.id for c in result.conflicts] == ["b/root"]


def test_no_own_license_is_judged_on_the_lineage():
    result = evaluate("me/q", facts(None, [ancestor("a/root", ["me/q", "a/root"], ["QUANTIZED_FROM"], APACHE)]), 6)
    assert result.inherited_from == "a/root"
    assert result.verdict == Verdict.OK


def test_no_license_anywhere_needs_review():
    result = evaluate("me/x", facts(None, [ancestor("a/root", ["me/x", "a/root"], ["ADAPTER_OF"])]), 6)
    assert result.verdict == Verdict.NEEDS_REVIEW
    assert result.inherited_from is None
    assert "Neither the model nor any ancestor" in result.explanation


def test_ancestor_without_license_needs_review():
    result = evaluate("me/x", facts(APACHE, [ancestor("a/root", ["me/x", "a/root"], ["FINETUNED_FROM"])]), 6)
    assert result.verdict == Verdict.NEEDS_REVIEW
    assert not result.conflicts  # nothing stricter is *declared*
    assert "declare no license" in result.explanation


def test_unlisted_ancestor_flags_incomplete_lineage():
    rows = [ancestor("ghost/root", ["me/x", "ghost/root"], ["FINETUNED_FROM"], unlisted=True)]
    result = evaluate("me/x", facts(APACHE, rows), 6)
    assert result.incomplete_lineage is True
    assert "not in the public dump" in result.explanation


def test_lineage_deeper_than_the_limit_is_incomplete():
    rows = [ancestor("a/mid", ["me/x", "a/mid"], ["FINETUNED_FROM"], APACHE)]
    result = evaluate("me/x", facts(APACHE, rows, lineage_depth=9), depth_limit=6)
    assert result.incomplete_lineage is True
    assert result.depth_limit == 6
    assert "deeper than 6 generations" in result.explanation


def test_non_commercial_training_data_needs_review_but_is_not_a_ban():
    data = [dataset_row("ds/nc", "me/x", 0, NON_COMMERCIAL)]
    result = evaluate("me/x", facts(APACHE, datasets=data), 6)
    assert result.verdict == Verdict.NEEDS_REVIEW
    assert result.training_data[0].id == "ds/nc"
    assert "ds/nc" in result.explanation


def test_training_data_without_license_is_ignored():
    data = [dataset_row("ds/unknown", "me/x", 0, None, listed=False)]
    assert evaluate("me/x", facts(APACHE, datasets=data), 6).verdict == Verdict.OK


def test_training_data_never_improves_a_bad_verdict():
    rows = [ancestor("b/root", ["me/x", "b/root"], ["FINETUNED_FROM"], NON_COMMERCIAL)]
    data = [dataset_row("ds/nc", "me/x", 0, NON_COMMERCIAL)]
    assert evaluate("me/x", facts(APACHE, rows, data), 6).verdict == Verdict.NOT_ALLOWED


def test_ancestors_are_sorted_closest_first():
    rows = [
        ancestor("far", ["me", "mid", "far"], ["FINETUNED_FROM", "MERGED_FROM"], APACHE),
        ancestor("mid", ["me", "mid"], ["FINETUNED_FROM"], APACHE),
    ]
    sorted_facts = facts(APACHE, rows)
    assert [a.id for a in sorted_facts.ancestors] == ["mid", "far"]
    assert [r.value for r in sorted_facts.ancestors[1].relations] == ["finetune", "merge"]
