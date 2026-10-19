"""Agent tools on the fake graph, and the Ollama client on a mocked HTTP transport."""

import json

import httpx
import pytest

from app.agent.llm import OllamaChat
from app.agent.tools import TOOL_NAMES, TOOL_SPECS, AgentTools
from app.domain import Evidence
from app.errors import ServiceUnavailableError
from tests.conftest import FakeNeo4jClient
from tests.fake_graph import FakeRepository
from tests.fake_llm import ScriptedLLM


@pytest.fixture(name="tools")
def fixture_tools(settings):
    llm = ScriptedLLM([{"content": "MATCH (m:Model) RETURN m.id AS id ORDER BY m.downloads DESC", "tool_calls": []}])
    return AgentTools(FakeRepository(), FakeNeo4jClient(rows=[{"id": "acme/base"}]), settings, llm)


def test_every_tool_has_a_spec_and_a_handler(tools):
    assert len(TOOL_SPECS) == len(TOOL_NAMES) == 9
    for spec in TOOL_SPECS:
        assert spec["function"]["parameters"]["type"] == "object"
    assert set(TOOL_NAMES) == set(tools._handlers)  # pylint: disable=protected-access


@pytest.mark.parametrize(
    "name, arguments, evidence",
    [
        ("search_models", {"query": "chat"}, Evidence.VERIFIED),
        ("model_details", {"model_id": "acme/chat"}, Evidence.VERIFIED),
        ("lineage", {"model_id": "bob/chat-GGUF"}, Evidence.VERIFIED),
        ("recommend", {"task": "text-generation", "commercial_use": True}, Evidence.DERIVED),
        ("similar_models", {"model_id": "acme/chat"}, Evidence.DERIVED),
        ("predicted_parents", {"model_id": "carl/lora"}, Evidence.HYPOTHESIS),
        ("influential_models", {}, Evidence.DERIVED),
        ("text_to_cypher", {"question": "most downloaded models"}, Evidence.DERIVED),
    ],
)
def test_tools_return_labelled_facts(tools, name, arguments, evidence):
    output = tools.run(name, arguments)
    assert output.success, output.content
    assert output.facts
    assert output.facts[0].evidence == evidence
    assert all(f.source == name for f in output.facts)
    json.loads(output.content)  # the LLM always gets valid JSON


def test_text_to_cypher_tool_keeps_its_attempts(tools):
    output = tools.run("text_to_cypher", {"question": "top models"})
    assert output.cypher.succeeded
    assert output.cypher.attempts[0].fixes == ["added LIMIT 25"]


def test_bad_calls_become_failed_outputs(tools):
    assert not tools.run("nope", {}).success
    assert "bad arguments" in tools.run("lineage", {"wrong": 1}).content
    assert not tools.run("model_details", {"model_id": "nobody/nothing"}).success


def _ollama(handler, settings):
    return OllamaChat(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_ollama_chat_parses_tool_calls(settings):
    sent = {}

    def handler(request):
        sent.update(json.loads(request.content))
        message = {
            "content": "",
            "tool_calls": [
                {"function": {"name": "lineage", "arguments": {"model_id": "a/b"}}},
                {"function": {"name": "search_models", "arguments": '{"query": "x"}'}},
                {"function": {"name": "broken", "arguments": "{not json"}},
            ],
        }
        return httpx.Response(200, json={"message": message})

    reply = _ollama(handler, settings).chat([{"role": "user", "content": "q"}], tools=TOOL_SPECS)
    assert reply["tool_calls"] == [
        {"name": "lineage", "arguments": {"model_id": "a/b"}},
        {"name": "search_models", "arguments": {"query": "x"}},
        {"name": "broken", "arguments": {}},
    ]
    assert sent["options"]["temperature"] == 0
    assert sent["model"] == settings.ollama_model and sent["stream"] is False


def test_ollama_json_output(settings):
    def handler(request):
        assert json.loads(request.content)["format"]["type"] == "object"
        return httpx.Response(200, json={"message": {"content": '{"answer": "hi", "fact_ids": [], "in_scope": true}'}})

    assert _ollama(handler, settings).json([], {"type": "object"})["answer"] == "hi"
    bad = _ollama(lambda r: httpx.Response(200, json={"message": {"content": "not json"}}), settings)
    assert bad.json([], {"type": "object"}) == {}


def test_ollama_down_is_a_503(settings):
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ServiceUnavailableError, match="make ollama-pull"):
        _ollama(handler, settings).chat([])
