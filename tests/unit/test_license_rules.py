"""The license verdict rule, tested without any database."""

import pytest

from app.models.catalog import LicenseInfo
from app.models.license import Verdict
from app.services.license import build_ancestors, evaluate, license_status, worst
from tests.fake_graph import APACHE, NON_COMMERCIAL, ancestor

LLAMA = {"id": "llama3.1", "family": "community", "commercial_use": "conditional", "url": None, "notes": None}


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
    result = evaluate("acme/base", LicenseInfo(**APACHE), [])
    assert result.verdict == Verdict.OK
    assert "root model" in result.explanation


def test_finetune_cannot_remove_a_parent_restriction():
    ancestors = build_ancestors([ancestor("meta/llama", ["me/ft", "meta/llama"], ["FINETUNED_FROM"], LLAMA)])
    result = evaluate("me/ft", LicenseInfo(**APACHE), ancestors)
    assert result.verdict == Verdict.CONDITIONAL
    assert len(result.conflicts) == 1
    assert "descends from meta/llama" in result.conflicts[0].reason


def test_non_commercial_ancestor_wins():
    rows = [
        ancestor("a/mid", ["me/x", "a/mid"], ["FINETUNED_FROM"], APACHE),
        ancestor("b/root", ["me/x", "a/mid", "b/root"], ["FINETUNED_FROM", "FINETUNED_FROM"], NON_COMMERCIAL),
    ]
    result = evaluate("me/x", LicenseInfo(**APACHE), build_ancestors(rows))
    assert result.verdict == Verdict.NOT_ALLOWED
    assert [c.ancestor.id for c in result.conflicts] == ["b/root"]


def test_no_license_anywhere_needs_review():
    rows = [ancestor("a/root", ["me/x", "a/root"], ["ADAPTER_OF"])]
    result = evaluate("me/x", None, build_ancestors(rows))
    assert result.verdict == Verdict.NEEDS_REVIEW
    assert result.inherited_from is None
    assert "Neither the model nor any ancestor" in result.explanation


def test_ancestor_without_license_needs_review():
    rows = [ancestor("a/root", ["me/x", "a/root"], ["FINETUNED_FROM"])]
    result = evaluate("me/x", LicenseInfo(**APACHE), build_ancestors(rows))
    assert result.verdict == Verdict.NEEDS_REVIEW
    assert not result.conflicts  # nothing stricter is *declared*
    assert "declare no license" in result.explanation


def test_unlisted_ancestor_flags_incomplete_lineage():
    rows = [ancestor("ghost/root", ["me/x", "ghost/root"], ["FINETUNED_FROM"], unlisted=True)]
    result = evaluate("me/x", LicenseInfo(**APACHE), build_ancestors(rows))
    assert result.incomplete_lineage is True
    assert "not in the public dump" in result.explanation


def test_ancestors_are_sorted_closest_first():
    rows = [
        ancestor("far", ["me", "mid", "far"], ["FINETUNED_FROM", "MERGED_FROM"], APACHE),
        ancestor("mid", ["me", "mid"], ["FINETUNED_FROM"], APACHE),
    ]
    ancestors = build_ancestors(rows)
    assert [a.id for a in ancestors] == ["mid", "far"]
    assert ancestors[1].relations == ["finetune", "merge"]
