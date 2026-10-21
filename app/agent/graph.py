"""The agent itself: a LangGraph state machine.

    screen ──refused──> refuse ──> END
      │
      v
    plan <──── act          plan = the LLM picks tools (or stops)
      │  └tools┘            act  = we run them and keep their facts
      v
    answer ──> verify ──> END    answer = structured JSON that cites facts by number
                                 verify = we check the citations and compute the confidence

The LLM never talks to Neo4j directly: it can only call the tools in tools.py,
and its final answer is checked against the facts those tools returned.
"""

import json
import re
import time
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.agent.input_guard import screen_question
from app.agent.llm import ChatModel
from app.agent.tools import TOOL_SPECS, AgentTools, Fact
from app.config import Settings
from app.database.neo4j_client import Neo4jClient
from app.domain import Evidence
from app.repositories.graph_repository import GraphRepository

MAX_ROUNDS = 4  # planning rounds (each can call up to MAX_CALLS_PER_ROUND tools)
MAX_CALLS_PER_ROUND = 3
_FRENCH_WORDS = (
    r"\b(le|la|les|des|du|une?|est|sont|quel(le)?s?|quels|peux|puis|pour|avec|dans|mod[eè]les?|combien|"
    r"comment|pourquoi|licence|utiliser|je|tu|nous|vous)\b"
)
_FRENCH = re.compile(_FRENCH_WORDS, re.IGNORECASE)
_LEAKED_FIELDS = re.compile(r"[,;]?\s*\(?\b(fact_ids|in_scope)\b\s*[=:].*$", re.IGNORECASE | re.DOTALL)
OUT_OF_SCOPE = {
    "English": "I can only answer questions about AI models, their lineage, licenses and datasets.",
    "French": "Je ne réponds qu'aux questions sur les modèles d'IA, leur lignée, leurs licences et leurs datasets.",
}
_MODEL_ID = re.compile(r"\b[A-Za-z0-9][\w.-]*/[\w.-]*[A-Za-z0-9]\b")

PLAN_PROMPT = """You are the ModelAtlas assistant. You answer questions about the open AI model ecosystem stored in
a Neo4j graph: Hugging Face models, their parents (fine-tunes, quantized copies, adapters, merges), publishers,
licenses, training datasets, papers.

How to work:
- get facts with the tools, never from memory; never invent a model id;
- if the user gives an approximate model name, call search_models first and use the exact id it returns;
- prefer the specific tools; use text_to_cypher only for questions no other tool covers (counts, rankings...);
- call several tools when the question needs it (e.g. lineage then license_check);
- when you have enough facts, stop calling tools and reply with one short sentence;
- if the question is not about AI models, datasets or licenses, do not call any tool."""

ANSWER_PROMPT = """Write the final answer to the user's question using ONLY the numbered facts below.
- Cite the numbers of the facts you used in fact_ids.
- If the facts do not answer the question, say so honestly.
- Facts labelled HYPOTHESIS are machine-learning guesses: present them as "probably", never as certain.
- License verdicts are informational, not legal advice.
- in_scope is false when the question is not about AI models, datasets, papers or licenses.
- Answer in 1 to 5 sentences, in the language given below (never in another one)."""

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "fact_ids": {"type": "array", "items": {"type": "integer"}},
        "in_scope": {"type": "boolean"},
    },
    "required": ["answer", "fact_ids", "in_scope"],
}

REFUSALS = {
    "write_request": "I can't do that: ModelAtlas is read-only, so I never create, change or delete data.",
    "prompt_injection": "I can't follow instructions that try to change my rules. Ask me about AI models instead.",
    "secrets": "I never share passwords, API keys or configuration.",
}


def answer_language(question: str) -> str:
    """French or English. Qwen tends to switch to Chinese when unsure, so we say it explicitly."""
    return "French" if len(_FRENCH.findall(question)) >= 2 else "English"


class AgentState(TypedDict, total=False):
    """Everything the nodes share. Each node returns the keys it changes."""

    question: str
    messages: list[dict]
    pending: list[dict]
    facts: list[Fact]
    steps: list[dict]
    cypher: list[dict]
    rounds: int
    draft: dict
    refusal: str | None
    result: dict


def score_confidence(facts: list[Fact], cited: list[int], steps: list[dict], cypher: list[dict], unknown: list[str]):
    """Confidence from what we can check, not from the LLM's own opinion.

    Start high, then lose points for every reason to doubt: no citation, failed
    tools, LLM-written Cypher, repairs, unknown model ids, ML guesses.
    Returns (confidence, status, reasons).
    """
    if not facts:
        return 0.2, "no_answer", ["no tool returned a fact"]
    confidence, reasons = 0.95, []
    if cited:
        reasons.append(f"the answer cites {len(cited)} fact(s) returned by the tools")
    else:
        confidence = 0.4
        reasons.append("the answer cites no fact")
    failed = sum(1 for step in steps if not step["success"])
    if failed:
        confidence -= 0.05 * failed
        reasons.append(f"{failed} tool call(s) failed")
    if any(step["tool"] == "text_to_cypher" for step in steps):
        confidence -= 0.1
        reasons.append("used an LLM-written Cypher query (validated, but it may not mean exactly what was asked)")
    repairs = sum(1 for attempt in cypher if attempt["stage"] != "ok")
    if repairs:
        confidence -= 0.05 * repairs
        reasons.append(f"{repairs} generated quer{'y' if repairs == 1 else 'ies'} had to be repaired")
    if unknown:
        confidence -= 0.25 * len(unknown)
        reasons.append(f"{len(unknown)} model id(s) in the answer are not in the graph")
    guesses = {facts[i - 1].source for i in cited if facts[i - 1].evidence == Evidence.HYPOTHESIS}
    if guesses:
        confidence = min(confidence, 0.6)
        if "text_to_cypher" in guesses:
            reasons.append("relies on the result of an LLM-written query (HYPOTHESIS: check the query shown)")
        if guesses - {"text_to_cypher"}:
            reasons.append("relies on a machine-learning guess (HYPOTHESIS)")
    return confidence, "answered", reasons


class ModelAtlasAgent:
    """Build the graph once, then `ask()` as many questions as you want."""

    def __init__(self, repo: GraphRepository, db: Neo4jClient, settings: Settings, llm: ChatModel):
        self.llm = llm
        self.repo = repo  # the API's repository (services); `db` is only for text-to-Cypher
        self.tools = AgentTools(repo, db, settings, llm)
        self.graph = self._build()

    def _build(self):
        graph = StateGraph(AgentState)
        graph.add_node("screen", self.screen)
        graph.add_node("refuse", self.refuse)
        graph.add_node("plan", self.plan)
        graph.add_node("act", self.act)
        graph.add_node("answer", self.answer)
        graph.add_node("verify", self.verify)
        graph.add_edge(START, "screen")
        graph.add_conditional_edges("screen", lambda s: "refuse" if s.get("refusal") else "plan")
        graph.add_conditional_edges("plan", lambda s: "act" if s.get("pending") else "answer")
        graph.add_edge("act", "plan")
        graph.add_edge("answer", "verify")
        graph.add_edge("verify", END)
        graph.add_edge("refuse", END)
        return graph.compile()

    # ------------------------------------------------------------ nodes

    def screen(self, state: AgentState) -> dict:
        """Regex guardrail before any LLM call."""
        return {"refusal": screen_question(state["question"])}

    def refuse(self, state: AgentState) -> dict:
        """Stop with a clear reason, no LLM, no database."""
        code, reason = state["refusal"].split(": ", 1)
        return {
            "result": {
                "status": "refused",
                "answer": REFUSALS.get(code, "I can't answer that."),
                "refusal_reason": reason,
                "confidence": None,
                "confidence_reasons": [f"refused by the input guardrail ({code})"],
                "fact_ids": [],
                "warnings": [],
            }
        }

    def plan(self, state: AgentState) -> dict:
        """Ask the LLM which tools to call next (or none)."""
        rounds = state.get("rounds", 0)
        if rounds >= MAX_ROUNDS:
            return {"pending": []}
        messages = state.get("messages") or [
            {"role": "system", "content": PLAN_PROMPT},
            {"role": "user", "content": state["question"]},
        ]
        reply = self.llm.chat(messages, tools=TOOL_SPECS)
        calls = reply.get("tool_calls", [])[:MAX_CALLS_PER_ROUND]
        messages = messages + [
            {
                "role": "assistant",
                "content": reply.get("content", ""),
                "tool_calls": [{"function": {"name": c["name"], "arguments": c["arguments"]}} for c in calls],
            }
        ]
        return {"messages": messages, "pending": calls, "rounds": rounds + 1}

    def act(self, state: AgentState) -> dict:
        """Run the pending tool calls and keep their facts."""
        facts, steps, cypher = list(state.get("facts", [])), list(state.get("steps", [])), list(state.get("cypher", []))
        messages = list(state["messages"])
        for call in state["pending"]:
            arguments = call.get("arguments") or {}
            output = self.tools.run(call["name"], arguments)
            first = len(facts) + 1
            facts += output.facts
            numbered = [f"[{first + i}] ({f.evidence.value}) {f.statement}" for i, f in enumerate(output.facts)]
            steps.append(
                {"tool": call["name"], "arguments": arguments, "success": output.success, "facts": len(output.facts)}
            )
            if output.cypher is not None:
                cypher += [
                    {"cypher": a.cypher, "stage": a.stage, "errors": a.errors, "fixes": a.fixes}
                    for a in output.cypher.attempts
                ]
            messages.append(
                {
                    "role": "tool",
                    "tool_name": call["name"],
                    "content": output.content + ("\nFacts:\n" + "\n".join(numbered) if numbered else ""),
                }
            )
        return {"facts": facts, "steps": steps, "cypher": cypher, "messages": messages, "pending": []}

    def answer(self, state: AgentState) -> dict:
        """Structured final answer that cites facts by number."""
        facts = state.get("facts", [])
        listing = "\n".join(f"[{i}] ({f.evidence.value}) {f.statement}" for i, f in enumerate(facts, start=1))
        messages = [
            {"role": "system", "content": ANSWER_PROMPT},
            {
                "role": "user",
                "content": f"Question: {state['question']}\n\nFacts:\n{listing or '(no facts)'}\n\n"
                f"Answer language: {answer_language(state['question'])}",
            },
        ]
        draft = self.llm.json(messages, ANSWER_SCHEMA)
        return {"draft": draft}

    def verify(self, state: AgentState) -> dict:
        """Check citations and model ids, compute a confidence we can explain."""
        facts, draft = state.get("facts", []), state.get("draft") or {}
        steps, cypher = state.get("steps", []), state.get("cypher", [])
        # small models sometimes copy the JSON fields into the text ("..., fact_ids=[1, 2].")
        text = _LEAKED_FIELDS.sub("", str(draft.get("answer") or "")).strip()
        warnings = []

        # Off-topic: the planner was told not to call any tool for those. A question where
        # tools were called but found nothing is "no_answer" instead.
        if not steps and (not draft.get("in_scope", True) or not facts):
            return {
                "result": {
                    "status": "out_of_scope",
                    "answer": OUT_OF_SCOPE[answer_language(state["question"])],
                    "confidence": None,
                    "confidence_reasons": ["the question is not about the model graph"],
                    "fact_ids": [],
                    "warnings": [],
                }
            }
        if not text:
            text = "Sorry, I could not write an answer from the facts I found."
            warnings.append("the LLM returned no valid structured answer")

        cited = []
        for raw in draft.get("fact_ids") or []:
            if isinstance(raw, int) and 1 <= raw <= len(facts) and raw not in cited:
                cited.append(raw)
            else:
                warnings.append(f"the answer cited fact [{raw}], which does not exist")
        unknown = self.unknown_models(text, facts, state["question"])
        warnings += [f"{m} is not a model of the graph (possible hallucination)" for m in unknown]

        confidence, status, reasons = score_confidence(facts, cited, steps, cypher, unknown)
        if len(cited) != len(draft.get("fact_ids") or []):
            confidence -= 0.1
            reasons.append("the answer cited facts that do not exist")
        return {
            "result": {
                "status": status,
                "answer": text,
                "confidence": round(min(max(confidence, 0.05), 0.95), 2) if status == "answered" else 0.2,
                "confidence_reasons": reasons,
                "fact_ids": cited,
                "warnings": warnings,
            }
        }

    def unknown_models(self, text: str, facts: list[Fact], question: str = "") -> list[str]:
        """Model ids written in the answer that no tool returned and that are not in the graph.

        Ids the user wrote in the question are not hallucinations: "foo/SuperGPT-900B is not in the
        graph" repeats the question.
        """
        # compare whole ids, not substrings: "foo/SuperGPT-900" is not "foo/SuperGPT-900B"
        known = set(_MODEL_ID.findall(" ".join(f.statement for f in facts) + " " + question))
        mentioned = [
            m for m in dict.fromkeys(_MODEL_ID.findall(text)) if all(re.search("[A-Za-z]", p) for p in m.split("/"))
        ]
        return [m for m in mentioned if m not in known and not self.repo.model_exists(m)]

    # ------------------------------------------------------------ entry point

    def ask(self, question: str) -> dict:
        """Run the graph; return everything the API response needs."""
        start = time.perf_counter()
        state = self.graph.invoke({"question": question}, {"recursion_limit": 4 * MAX_ROUNDS + 6})
        result = dict(state["result"])
        facts = state.get("facts", [])
        result.update(
            question=question,
            facts=[
                {"id": i, "statement": f.statement, "evidence": f.evidence, "source": f.source}
                for i, f in enumerate(facts, start=1)
            ],
            steps=state.get("steps", []),
            cypher=state.get("cypher", []),
            model=self.llm.name,
            elapsed_ms=round((time.perf_counter() - start) * 1000),
        )
        return result


def dumps(result: dict) -> str:
    """Readable JSON (used by the evaluation script)."""
    return json.dumps(result, indent=2, default=str)
