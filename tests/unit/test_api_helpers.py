"""Small pure helpers of the API layer."""

import re

import pytest

from app.domain import LINEAGE_PATTERN
from app.errors import BadRequestError
from app.repositories.query_store import QUERIES_DIR, load_query
from app.services.catalog import to_fulltext_query
from app.services.common import check_depth, to_summary
from app.services.lineage import decode_cursor, encode_cursor
from app.services.recommend import popularity_score

VAR_LENGTH = re.compile(r"\[[^\]\[]*\*[^\]\[]*\]")
QUERY_NAMES = sorted(p.stem for p in QUERIES_DIR.glob("*.cypher"))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Mistral 7B instruct", "mistral* AND 7b* AND instruct*"),
        ("llama-3.1", "llama* AND 3?1*"),
        ('qwen" OR 1=1 //', "qwen* AND or* AND 1* AND 1*"),
    ],
)
def test_fulltext_query_is_built_from_words_only(text, expected):
    assert to_fulltext_query(text) == expected


def test_fulltext_query_keeps_at_most_8_words():
    assert to_fulltext_query(" ".join(["word"] * 20)).count("AND") == 7


def test_fulltext_query_needs_a_word():
    with pytest.raises(BadRequestError):
        to_fulltext_query("*** ???")


def test_cursor_round_trip():
    assert decode_cursor(encode_cursor(1234, "org/model-v1")) == (1234, "org/model-v1")


@pytest.mark.parametrize("cursor", ["@@@", "aGVsbG8=", encode_cursor(1, "x")[:-4]])
def test_bad_cursor(cursor):
    with pytest.raises(BadRequestError):
        decode_cursor(cursor)


def test_check_depth(settings):
    assert check_depth(settings.max_traversal_depth, settings) == settings.max_traversal_depth
    with pytest.raises(BadRequestError):
        check_depth(settings.max_traversal_depth + 1, settings)


def test_to_summary_ignores_extra_keys():
    summary = to_summary({"id": "a/b", "downloads": 3, "descendant_count": 9, "junk": True})
    assert summary.id == "a/b" and summary.downloads == 3


def test_popularity_score():
    assert popularity_score(None, None) == 0
    assert popularity_score(999, 0) == 3
    assert popularity_score(10**6, 10) > popularity_score(10**5, 1000)


@pytest.mark.parametrize("name", QUERY_NAMES)
def test_every_query_loads_with_tokens_replaced(name):
    text = load_query(name, 3)
    assert "__" not in text.replace("__LINEAGE__", "")  # no token left
    assert "__LINEAGE__" not in text
    # every variable-length relationship must have an upper bound
    for pattern in VAR_LENGTH.findall(text):
        assert re.search(r"\*\d*\.\.3\]$", pattern), pattern


def test_lineage_queries_only_expand_lineage_types():
    assert f"[:{LINEAGE_PATTERN}*1..3]" in load_query("lineage_up", 3)


@pytest.mark.parametrize("depth", [None, 0, 13, "3", True])
def test_depth_must_be_a_small_int(depth):
    with pytest.raises(ValueError):
        load_query("lineage_up", depth)


def test_unknown_query():
    with pytest.raises(KeyError):
        load_query("drop_everything")
