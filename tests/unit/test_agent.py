"""The LangGraph agent on the fake graph, with a scripted LLM (no Neo4j, no Ollama)."""

import pytest

from app.agent.graph import MAX_ROUNDS, ModelAtlasAgent, answer_language, score_confidence
from app.agent.tools import TOOL_NAMES, Fact
from app.domain import Evidence
from tests.conftest import FakeNeo4jClient
from tests.fake_graph import FakeRepository
from tests.fake_llm import ScriptedLLM, call


@pytest.fixture(name="make_agent")
def fixture_make_agent(settings):
    def make(chat=None, answers=None):
        llm = ScriptedLLM(chat, answers)
        return ModelAtlasAgent(FakeRepository(), FakeNeo4jClient(), settings, llm), llm

    return make


def test_refused_questions_never_reach_the_llm(make_agent):
    agent, llm = make_agent()
    result = agent.ask("Delete every model from the graph")
    assert result["status"] == "refused"
    assert result["refusal_reason"].startswith("ModelAtlas is read-only")
    assert not llm.chat_calls and not llm.json_calls


def test_multi_step_answer_cites_verified_and_derived_facts(make_agent):
    agent, llm = make_agent(
        chat=[call("lineage", model_id="bob/chat-GGUF"), call("license_check", model_id="bob/chat-GGUF")],
        answers=[
            {"answer": "bob/chat-GGUF comes from acme/chat; check the license.", "fact_ids": [1, 3], "in_scope": True}
        ],
    )
    result = agent.ask("Where does bob/chat-GGUF come from and can I sell it?")
    assert result["status"] == "answered"
    assert [s["tool"] for s in result["steps"]] == ["lineage", "license_check"]
    assert all(s["success"] for s in result["steps"])
    assert result["fact_ids"] == [1, 3]
    evidences = {f["source"]: f["evidence"] for f in result["facts"]}
    assert evidences["lineage"] == Evidence.VERIFIED
    assert evidences["license_check"] in (Evidence.VERIFIED, Evidence.DERIVED)
    assert result["confidence"] == 0.95
    assert not result["warnings"]
    # the tool results went back to the LLM with numbered facts
    tool_message = llm.chat_calls[1]["messages"][-1]
    assert tool_message["role"] == "tool" and "[1] (VERIFIED)" in tool_message["content"]
    assert llm.chat_calls[0]["tools"][0]["function"]["name"] in TOOL_NAMES


def test_hallucinated_model_ids_are_flagged(make_agent):
    agent, _ = make_agent(
        chat=[call("search_models", query="chat")],
        answers=[{"answer": "Use acme/chat or ghost/model-9000.", "fact_ids": [1, 42], "in_scope": True}],
    )
    result = agent.ask("Which chat model should I use?")
    assert any("ghost/model-9000" in w for w in result["warnings"])
    assert any("[42]" in w for w in result["warnings"])
    assert result["confidence"] < 0.7


def test_hypotheses_cap_the_confidence(make_agent):
    agent, _ = make_agent(
        chat=[call("predicted_parents", model_id="carl/lora")],
        answers=[{"answer": "carl/lora is probably based on acme/chat.", "fact_ids": [1], "in_scope": True}],
    )
    result = agent.ask("What is carl/lora based on?")
    assert result["facts"][0]["evidence"] == Evidence.HYPOTHESIS
    assert result["confidence"] <= 0.6
    assert any("HYPOTHESIS" in r for r in result["confidence_reasons"])


def test_out_of_scope_questions(make_agent):
    agent, llm = make_agent(answers=[{"answer": "I only know about AI models.", "fact_ids": [], "in_scope": False}])
    result = agent.ask("What is the best pizza in Paris?")
    assert result["status"] == "out_of_scope"
    assert result["confidence"] is None
    assert len(llm.chat_calls) == 1  # one planning round, no tool


def test_failed_tools_are_reported_not_raised(make_agent):
    agent, _ = make_agent(
        chat=[call("model_details", model_id="nobody/nothing"), call("teleport", to="mars")],
        answers=[{"answer": "I could not find this model.", "fact_ids": [], "in_scope": True}],
    )
    result = agent.ask("Tell me about nobody/nothing")
    assert [s["success"] for s in result["steps"]] == [False, False]
    assert result["status"] == "no_answer"


def test_the_number_of_rounds_is_bounded(make_agent):
    agent, llm = make_agent(chat=[call("search_models", query="x")] * 20, answers=[{"answer": "ok", "fact_ids": [1]}])
    result = agent.ask("loop forever")
    assert len(llm.chat_calls) == MAX_ROUNDS
    assert len(result["steps"]) == MAX_ROUNDS


def test_invalid_structured_answer_falls_back(make_agent):
    agent, _ = make_agent(chat=[call("search_models", query="chat")], answers=[{}])
    result = agent.ask("Find chat models")
    assert "could not write an answer" in result["answer"]
    assert "no valid structured answer" in result["warnings"][0]


def test_score_confidence_rules():
    facts = [Fact("a", Evidence.VERIFIED, "x"), Fact("b", Evidence.DERIVED, "text_to_cypher")]
    steps = [{"tool": "text_to_cypher", "success": True}]
    cypher = [{"stage": "guard"}, {"stage": "ok"}]
    confidence, status, reasons = score_confidence(facts, [2], steps, cypher, [])
    assert status == "answered"
    assert confidence == pytest.approx(0.95 - 0.1 - 0.05)
    assert len(reasons) == 3
    assert score_confidence([], [], [], [], [])[1] == "no_answer"


def test_llm_written_query_results_are_hypotheses():
    # a valid, safe query can still answer another question (wrong publisher counts in the agent evaluation)
    facts = [Fact('query result: {"models": 1737}', Evidence.HYPOTHESIS, "text_to_cypher")]
    steps = [{"tool": "text_to_cypher", "success": True}]
    confidence, _, reasons = score_confidence(facts, [1], steps, [{"stage": "ok"}], [])
    assert confidence <= 0.6
    assert any("LLM-written query" in r for r in reasons)


def test_no_tool_and_no_fact_is_out_of_scope_even_if_the_llm_says_in_scope(make_agent):
    agent, _ = make_agent(answers=[{"answer": "No relevant facts.", "fact_ids": [], "in_scope": True}])
    assert agent.ask("What is the best pizza in Naples?")["status"] == "out_of_scope"


@pytest.mark.parametrize(
    "question, language",
    [
        ("Can I use TheBloke/Mistral-7B-GGUF commercially?", "English"),
        ("Est-ce que je peux utiliser ce modèle pour un produit commercial ?", "French"),
        ("Quels sont les modèles les plus téléchargés ?", "French"),
    ],
)
def test_answer_language(question, language):
    assert answer_language(question) == language


def test_the_answer_prompt_names_the_language(make_agent):
    agent, llm = make_agent(chat=[call("search_models", query="mistral")], answers=[{"answer": "ok", "fact_ids": [1]}])
    agent.ask("Quels modèles Mistral sont les plus utilisés ?")
    assert llm.json_calls[0]["messages"][-1]["content"].endswith("Answer language: French")


def test_json_fields_copied_into_the_text_are_removed(make_agent):
    agent, _ = make_agent(
        chat=[call("search_models", query="chat")],
        answers=[{"answer": "Use acme/chat, it is Apache-2.0., fact_ids=[1, 2].", "fact_ids": [1], "in_scope": True}],
    )
    assert agent.ask("Which chat model?")["answer"] == "Use acme/chat, it is Apache-2.0."


def test_out_of_scope_answer_is_our_own_sentence(make_agent):
    agent, _ = make_agent(answers=[{"answer": "Pizza Margherita!", "fact_ids": [], "in_scope": False}])
    assert "modèles d'IA" in agent.ask("Quelle est la meilleure pizza de Naples ?")["answer"]


def test_a_model_id_from_the_question_is_not_a_hallucination(make_agent):
    agent, _ = make_agent(
        chat=[call("model_details", model_id="foo/SuperGPT-900B")],
        answers=[{"answer": "foo/SuperGPT-900B is not in the graph.", "fact_ids": [], "in_scope": True}],
    )
    result = agent.ask("What is the license of foo/SuperGPT-900B?")
    assert not any("hallucination" in w for w in result["warnings"])


def test_a_shorter_invented_id_is_still_flagged(make_agent):
    # the question's id must match exactly: a substring of it is another, invented model
    agent, _ = make_agent(
        chat=[call("model_details", model_id="foo/SuperGPT-900B")],
        answers=[{"answer": "foo/SuperGPT-900 is available.", "fact_ids": [], "in_scope": True}],
    )
    result = agent.ask("What is the license of foo/SuperGPT-900B?")
    assert any("foo/SuperGPT-900 is not a model" in w for w in result["warnings"])
