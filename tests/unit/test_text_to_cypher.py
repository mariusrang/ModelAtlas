"""Text-to-Cypher: generation, the three validation layers and the repair loop."""

from neo4j.exceptions import ClientError, CypherSyntaxError

from app.agent.text_to_cypher import MAX_REPAIRS, TextToCypher
from tests.fake_llm import ScriptedLLM

GOOD = "MATCH (m:Model) RETURN m.id AS id ORDER BY m.downloads DESC LIMIT 3"


class FakeDb:
    """Records queries; can fail on EXPLAIN or on execution."""

    def __init__(self, rows=None, explain_error=None, run_error=None):
        self.rows = rows if rows is not None else [{"id": "acme/base"}]
        self.explain_error, self.run_error = explain_error, run_error
        self.queries: list[str] = []

    def read(self, cypher, params=None):  # pylint: disable=unused-argument
        self.queries.append(cypher)
        if cypher.startswith("EXPLAIN"):
            if self.explain_error:
                error, self.explain_error = self.explain_error, None
                raise error
            return []
        if self.run_error:
            raise self.run_error
        return self.rows


def reply(text):
    return {"content": text, "tool_calls": []}


def test_valid_query_is_explained_then_run():
    db = FakeDb()
    outcome = TextToCypher(ScriptedLLM([reply(f"```cypher\n{GOOD}\n```")]), db).run("top models")
    assert outcome.succeeded
    assert outcome.rows == [{"id": "acme/base"}]
    assert db.queries == [f"EXPLAIN {GOOD}", GOOD]
    assert outcome.repairs == 0


def test_rejected_query_is_sent_back_and_repaired():
    llm = ScriptedLLM([reply("MATCH (m:Model) DETACH DELETE m RETURN 1"), reply(GOOD)])
    db = FakeDb()
    outcome = TextToCypher(llm, db).run("remove everything")
    assert outcome.succeeded and outcome.repairs == 1
    assert outcome.attempts[0].stage == "guard"
    assert "read-only" in llm.chat_calls[1]["messages"][-1]["content"]
    assert not any("DELETE" in q for q in db.queries)  # never reached Neo4j


def test_explain_error_triggers_a_repair():
    db = FakeDb(explain_error=CypherSyntaxError("Invalid input 'RETRUN'"))
    outcome = TextToCypher(ScriptedLLM([reply(GOOD), reply(GOOD)]), db).run("q")
    assert outcome.succeeded
    assert outcome.attempts[0].stage == "explain"
    assert "Invalid input" in outcome.attempts[0].errors[0]


def test_gives_up_after_the_repairs():
    llm = ScriptedLLM([reply("CREATE (n:Model) RETURN n")] * 5)
    outcome = TextToCypher(llm, FakeDb()).run("q")
    assert not outcome.succeeded
    assert len(outcome.attempts) == 1 + MAX_REPAIRS
    assert len(llm.chat_calls) == 1 + MAX_REPAIRS


def test_the_read_session_is_the_last_safety_net():
    db = FakeDb(run_error=ClientError("Writing in read access mode not allowed"))
    outcome = TextToCypher(ScriptedLLM([reply(GOOD)] * 3), db).run("q")
    assert not outcome.succeeded
    assert outcome.attempts[-1].stage == "execute"


def test_the_prompt_contains_the_schema_and_few_shot_examples():
    llm = ScriptedLLM([reply(GOOD)])
    TextToCypher(llm, FakeDb()).run("q")
    messages = llm.chat_calls[0]["messages"]
    assert "QUANTIZED_FROM" in messages[0]["content"]
    assert [m["role"] for m in messages[1:5]] == ["user", "assistant", "user", "assistant"]
    assert messages[-1] == {"role": "user", "content": "q"}
