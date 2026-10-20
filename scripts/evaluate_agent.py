"""Run the agent on config/agent_eval.toml and write reports/agent_eval.md.

Usage (stack running, `make ollama-pull` done):
    python -m scripts.evaluate_agent              # make agent-eval
    python -m scripts.evaluate_agent --only trap  # only one kind of case

A case passes when the status is the expected one and, for answers, when one of
the expected tools was called and every expected word is in the answer or in a
cited fact. Run time: about 20 s per question on a laptop CPU.
"""

import argparse
import logging
import statistics
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from app.agent.graph import ModelAtlasAgent
from app.agent.llm import OllamaChat
from app.config import get_settings
from app.database.neo4j_client import Neo4jClient
from app.errors import AppError
from app.repositories.graph_repository import GraphRepository
from scripts.etl.sources import REPORTS_DIR

logger = logging.getLogger("modelatlas.agent_eval")
CASES_FILE = Path(__file__).resolve().parents[1] / "config" / "agent_eval.toml"
STATUS_FOR = {"answer": "answered", "refuse": "refused", "out_of_scope": "out_of_scope"}


@dataclass
class Case:
    """One evaluation question."""

    id: str
    question: str
    expect: str
    kind: str = "normal"
    tools: list[str] = field(default_factory=list)
    mentions: list[str] = field(default_factory=list)


def load_cases(path: Path = CASES_FILE) -> list[Case]:
    """Read and check the TOML file."""
    with path.open("rb") as handle:
        raw = tomllib.load(handle)["case"]
    cases = [Case(**entry) for entry in raw]
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case id in agent_eval.toml")
    for case in cases:
        if case.expect not in STATUS_FOR:
            raise ValueError(f"{case.id}: expect must be one of {sorted(STATUS_FOR)}")
    return cases


def grade(case: Case, result: dict) -> list[str]:
    """Why the case failed (empty list = passed)."""
    problems = []
    if result["status"] != STATUS_FOR[case.expect]:
        problems.append(f"status {result['status']}, expected {STATUS_FOR[case.expect]}")
    called = [step["tool"] for step in result.get("steps", [])]
    if case.tools and not set(case.tools) & set(called):
        problems.append(f"none of {case.tools} was called (called: {called or 'nothing'})")
    cited = set(result.get("fact_ids", []))
    text = " ".join([result.get("answer", "")] + [f["statement"] for f in result.get("facts", []) if f["id"] in cited])
    problems += [f"'{word}' missing" for word in case.mentions if word.lower() not in text.lower()]
    if result.get("warnings") and case.expect == "answer":
        problems += [f"warning: {w}" for w in result["warnings"] if "hallucination" in w]
    return problems


def run_case(agent: ModelAtlasAgent, case: Case) -> dict:
    """Ask one question; an exception counts as a failure, not a crash of the whole run."""
    try:
        result = agent.ask(case.question)
    except AppError as exc:
        result = {"status": "error", "answer": exc.detail, "steps": [], "facts": [], "elapsed_ms": 0}
    result["problems"] = grade(case, result)
    return result


def summary(cases: list[Case], results: list[dict]) -> dict:
    """Headline numbers of the report."""

    def share(selected: list) -> float | None:
        return sum(1 for _, r in selected if not r["problems"]) / len(selected) if selected else None

    pairs = list(zip(cases, results))
    traps = [(c, r) for c, r in pairs if c.kind == "trap"]
    normal = [(c, r) for c, r in pairs if c.kind != "trap"]
    attempts = [a for r in results for a in r.get("cypher", [])]
    generated = [r for r in results if r.get("cypher")]
    first_try = [r for r in generated if r["cypher"][0]["stage"] == "ok"]
    confidences = [r["confidence"] for r in results if r.get("confidence") is not None]
    return {
        "cases": len(cases),
        "passed": sum(1 for r in results if not r["problems"]),
        "normal questions passed": share(normal),
        "traps handled": share(traps),
        "generated Cypher queries": len(attempts),
        "rejected by the guardrails": sum(1 for a in attempts if a["stage"] != "ok"),
        "text-to-Cypher valid at first try": len(first_try) / len(generated) if generated else None,
        "hallucinated model ids flagged": sum(
            1 for r in results for w in r.get("warnings", []) if "hallucination" in w
        ),
        "mean confidence (answered)": statistics.mean(confidences) if confidences else None,
        "median time per question (s)": statistics.median(r["elapsed_ms"] for r in results) / 1000 if results else 0,
    }


def fmt(value) -> str:
    """Numbers for the report."""
    if value is None:
        return "-"
    return f"{value:.2f}" if isinstance(value, float) else str(value)


def render_report(cases: list[Case], results: list[dict], model: str) -> str:
    """Markdown report."""
    lines = [
        "# Agent evaluation",
        f"Generated by `make agent-eval` with the local model `{model}`, temperature 0. Cases: "
        "[`config/agent_eval.toml`](../config/agent_eval.toml).",
        "## Summary",
        *[f"- {key}: **{fmt(value)}**" for key, value in summary(cases, results).items()],
        "## Cases",
        "| id | kind | expected | status | tools called | confidence | result |",
        "|---|---|---|---|---|---|---|",
    ]
    for case, result in zip(cases, results):
        tools = ", ".join(s["tool"] for s in result.get("steps", [])) or "-"
        verdict = "pass" if not result["problems"] else "FAIL: " + "; ".join(result["problems"])
        lines.append(
            f"| {case.id} | {case.kind} | {case.expect} | {result['status']} | {tools} | "
            f"{fmt(result.get('confidence'))} | {verdict} |"
        )
    lines.append("## Answers")
    for case, result in zip(cases, results):
        answer = str(result.get("answer", "")).replace("\n", " ")
        lines.append(f"- **{case.id}** ({case.question}) → {answer}")
        for attempt in result.get("cypher", []):
            cypher = attempt["cypher"].replace("\n", " ")
            lines.append(f"  - Cypher ({attempt['stage']}): `{cypher}` {'; '.join(attempt['errors'])}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--uri", default="bolt://localhost:7687")
    parser.add_argument("--ollama", default="http://localhost:11434")
    parser.add_argument("--only", choices=["normal", "trap"], help="run only one kind of case")
    args = parser.parse_args(argv)

    cases = [c for c in load_cases() if args.only in (None, c.kind)]
    # .env holds the docker service names (neo4j, ollama); this script runs on the host
    settings = get_settings().model_copy(update={"neo4j_uri": args.uri, "ollama_base_url": args.ollama})
    client = Neo4jClient(settings)
    llm = OllamaChat(settings)
    agent = ModelAtlasAgent(GraphRepository(client), client, settings, llm)
    results = []
    try:
        for case in cases:
            result = run_case(agent, case)
            logger.info(f"{case.id}: {result['status']} {'ok' if not result['problems'] else result['problems']}")
            results.append(result)
    finally:
        client.close()
    REPORTS_DIR.mkdir(exist_ok=True)
    (REPORTS_DIR / "agent_eval.md").write_text(render_report(cases, results, llm.name), encoding="utf-8")
    passed = sum(1 for r in results if not r["problems"])
    logger.info(f"{passed}/{len(results)} cases passed -> reports/agent_eval.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
