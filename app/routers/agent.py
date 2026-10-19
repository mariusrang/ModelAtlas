"""The LangGraph agent behind POST /agent/ask (rate-limited by Nginx: 10 requests/min per IP)."""

from fastapi import APIRouter, Depends

from app.agent.graph import ModelAtlasAgent
from app.dependencies import get_agent
from app.models.agent import AgentFact, AskRequest, AskResponse

router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/ask", response_model=AskResponse, summary="Ask a question in natural language")
def ask(body: AskRequest, agent: ModelAtlasAgent = Depends(get_agent)) -> AskResponse:
    """Multi-step agent (local LLM via Ollama) that calls graph tools and cites their facts.

    Guardrails: question screening, read-only and schema-checked Cypher (EXPLAIN before running),
    READ session with a timeout, citation check and a computed confidence. Facts keep their evidence
    label, so a verified fact and an ML guess are never mixed up.
    """
    result = agent.ask(body.question)
    cited = set(result["fact_ids"])
    facts = [AgentFact(**fact, cited=fact["id"] in cited) for fact in result["facts"]]
    payload = {key: value for key, value in result.items() if key not in ("facts", "fact_ids")}
    return AskResponse(**payload, facts=facts)
