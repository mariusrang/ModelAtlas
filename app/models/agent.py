"""Schemas of the agent endpoint (structured output, never free text only)."""

from typing import Literal

from pydantic import BaseModel, Field

from app.domain import Evidence


class AskRequest(BaseModel):
    """A question in natural language."""

    question: str = Field(
        min_length=3,
        max_length=500,
        examples=["Can I use TheBloke/Mistral-7B-Instruct-v0.2-GGUF in a commercial product?"],
    )


class AgentFact(BaseModel):
    """A fact returned by a tool. The answer may only rely on these."""

    id: int
    statement: str
    evidence: Evidence = Field(
        description="VERIFIED (card), DERIVED (our rule), HYPOTHESIS (ML guess or LLM-written query)"
    )
    source: str = Field(description="Tool that produced it")
    cited: bool = Field(default=False, description="True when the answer uses this fact")


class ToolStep(BaseModel):
    """One tool call made by the agent, in order."""

    tool: str
    arguments: dict
    success: bool
    facts: int = Field(description="How many facts the call returned")


class CypherAttemptOut(BaseModel):
    """One LLM-generated Cypher query and what the guardrails said."""

    cypher: str
    stage: str = Field(description="ok, or where it was stopped: guard, explain, execute")
    errors: list[str] = []
    fixes: list[str] = Field(default=[], description="What the guard changed, e.g. 'added LIMIT 25'")


class AskResponse(BaseModel):
    """The answer, and everything needed to check it."""

    question: str
    status: Literal["answered", "no_answer", "refused", "out_of_scope"]
    answer: str
    answer_evidence: Evidence = Field(
        default=Evidence.HYPOTHESIS,
        description="The answer text is written by the LLM; trust the facts it cites, each with its own label",
    )
    confidence: float | None = Field(
        default=None, ge=0, le=1, description="Computed from checks (citations, tools, validation), not by the LLM"
    )
    confidence_reasons: list[str] = []
    refusal_reason: str | None = None
    facts: list[AgentFact] = []
    steps: list[ToolStep] = Field(default=[], description="Tool calls in the order the agent made them")
    cypher: list[CypherAttemptOut] = Field(default=[], description="Generated queries, including rejected ones")
    warnings: list[str] = []
    model: str = Field(description="Local LLM used (Ollama)")
    elapsed_ms: int
