"""Static guardrail on LLM-generated Cypher."""

import pytest

from app.agent.cypher_guard import DEFAULT_LIMIT, MAX_LIMIT, check_cypher

GOOD = "MATCH (m:Model)-[:PUBLISHED_BY]->(o:Organization) RETURN o.id AS org, count(m) AS n ORDER BY n DESC"


def test_a_good_query_passes_and_gets_a_limit():
    result = check_cypher(GOOD)
    assert result.passed
    assert result.cypher.endswith(f"LIMIT {DEFAULT_LIMIT}")
    assert result.fixes == [f"added LIMIT {DEFAULT_LIMIT}"]


def test_a_too_big_limit_is_lowered():
    result = check_cypher(GOOD + " LIMIT 5000")
    assert result.passed
    assert result.cypher.endswith(f"LIMIT {MAX_LIMIT}")


def test_fences_and_trailing_semicolon_are_removed():
    result = check_cypher(f"```cypher\n{GOOD} LIMIT 5;\n```")
    assert result.passed
    assert result.cypher == f"{GOOD} LIMIT 5"


@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH (m:Model) DETACH DELETE m RETURN 1",
        "MATCH (m:Model) SET m.downloads = 0 RETURN m.id",
        "MATCH (m:Model) REMOVE m.task RETURN m.id",
        "CREATE (m:Model {id: 'x'}) RETURN m.id",
        "MERGE (m:Model {id: 'x'}) RETURN m.id",
        "MATCH (m:Model) WITH m FOREACH (x IN [1] | SET m.likes = 1) RETURN m.id",
        "LOAD CSV FROM 'http://evil' AS row RETURN row",
        "DROP INDEX model_task",
        "MATCH (m:Model) RETURN m.id UNION MATCH (d:Dataset) RETURN d.id",
    ],
)
def test_writes_are_rejected(cypher):
    result = check_cypher(cypher)
    assert not result.passed
    assert any("read-only" in e or "must start" in e for e in result.errors)


@pytest.mark.parametrize(
    "cypher",
    [
        "CALL db.labels() YIELD label RETURN label",
        "CALL apoc.periodic.iterate('a', 'b', {}) YIELD batches RETURN batches",
        "MATCH (m:Model) RETURN apoc.text.join([m.id], ',') AS x",
        "CALL gds.graph.drop('lineage') YIELD graphName RETURN graphName",
    ],
)
def test_procedures_are_rejected(cypher):
    assert any("procedure" in e for e in check_cypher(cypher).errors)


def test_one_statement_and_no_comments():
    assert "only one statement is allowed" in check_cypher(f"{GOOD}; MATCH (n) RETURN n").errors
    assert "comments are not allowed" in check_cypher(f"{GOOD} // hidden").errors


def test_keywords_inside_strings_are_fine():
    result = check_cypher("MATCH (m:Model) WHERE m.id CONTAINS 'delete-me; // set' RETURN m.id AS id LIMIT 3")
    assert result.passed, result.errors


def test_schema_allowlist():
    errors = check_cypher("MATCH (u:User)-[:FOLLOWS]->(m:Model) RETURN m.secret AS s").errors
    assert any("unknown label :User" in e for e in errors)
    assert any("unknown relationship type :FOLLOWS" in e for e in errors)
    assert any("unknown property .secret" in e for e in errors)


def test_maps_projections_subqueries_and_label_predicates_are_understood():
    cypher = (
        "MATCH (m:Model {id: 'mistralai/Mistral-7B-v0.1'})<-[:QUANTIZED_FROM|FINETUNED_FROM]-(c:Model) "
        "WHERE NOT c:Unlisted AND c.created_at.year >= 2024 "
        "CALL (c) { MATCH (c)-[:HAS_LICENSE]->(l:License) RETURN l.id AS license } "
        "RETURN c {.id, .downloads} AS child, license, COUNT { (c)<-[:ADAPTER_OF]-(:Model) } AS adapters "
        "ORDER BY c.downloads DESC LIMIT 10"
    )
    result = check_cypher(cypher)
    assert result.passed, result.errors
    assert not result.fixes


@pytest.mark.parametrize(
    "pattern, ok",
    [
        ("-[:FINETUNED_FROM*1..3]->", True),
        ("-[:FINETUNED_FROM*2]->", True),
        ("-[:FINETUNED_FROM*]->", False),
        ("-[:FINETUNED_FROM*2..]->", False),
        ("-[:FINETUNED_FROM*1..50]->", False),
    ],
)
def test_variable_length_needs_a_bound(pattern, ok):
    result = check_cypher(f"MATCH (m:Model){pattern}(p:Model) RETURN p.id AS id")
    assert result.passed is ok, result.errors


def test_empty_and_non_read_queries():
    assert check_cypher("  ").errors == ["empty query"]
    assert any("must RETURN" in e for e in check_cypher("MATCH (m:Model) WITH m").errors)


def test_a_quote_inside_backticks_cannot_hide_a_write():
    # a quote inside backticks used to open a fake string that hid DELETE from the keyword check
    result = check_cypher("MATCH (`x'`:Model) DETACH DELETE `x'` RETURN 1")
    assert not result.passed
    assert any("DELETE" in e for e in result.errors)


def test_backtick_names_are_still_checked_against_the_schema():
    assert check_cypher("MATCH (m:`Model`) RETURN m.id AS id").passed
    assert any("unknown label :Secret" in e for e in check_cypher("MATCH (m:`Secret`) RETURN m.id AS id").errors)


def test_unlisted_property_gets_a_hint():
    errors = check_cypher("MATCH (m:Model) WHERE m.unlisted = false RETURN count(m) AS n").errors
    assert any("WHERE NOT m:Unlisted" in e for e in errors)


@pytest.mark.parametrize(
    "cypher, ok",
    [
        ("MATCH (m:Model) RETURN m", False),
        ("MATCH (m:Model) RETURN DISTINCT m AS model", False),
        ("MATCH (m:Model)-[r:FINETUNED_FROM]->(p:Model) RETURN m.id AS id, r", False),
        ("MATCH p = (m:Model)-[:FINETUNED_FROM*1..2]->(:Model) RETURN p", False),
        ("MATCH (m:Model) RETURN collect(m) AS models", False),
        ("MATCH (m:Model) RETURN *", False),
        ("MATCH (m:Model) RETURN m.id AS model, m.downloads AS downloads ORDER BY downloads DESC", True),
        ("MATCH (m:Model) RETURN count(m) AS models", True),
        ("MATCH (m:Model) WITH m ORDER BY m.downloads DESC RETURN collect(m.id) AS ids", True),
    ],
)
def test_whole_nodes_are_not_returned(cypher, ok):
    # a Model node carries two 256-number embeddings: the prompt asks for plain values, the guard checks it
    assert check_cypher(cypher).passed is ok, check_cypher(cypher).errors
