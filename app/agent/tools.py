"""Tools the agent can call. Each one wraps an existing API service, so the
agent gets exactly the same answers (and evidence labels) as the endpoints.

A tool returns a short JSON text for the LLM and a list of facts. Facts are
what the final answer is allowed to cite: each has an evidence label
(VERIFIED / DERIVED / HYPOTHESIS) and the tool it came from.
"""

import json
from dataclasses import dataclass, field
from typing import Callable

from app.agent.llm import ChatModel
from app.agent.text_to_cypher import CypherOutcome, TextToCypher
from app.config import Settings
from app.database.neo4j_client import Neo4jClient
from app.domain import Evidence
from app.errors import AppError
from app.models.recommend import RecommendRequest
from app.repositories.graph_repository import GraphRepository
from app.services.analytics import AnalyticsService
from app.services.catalog import CatalogService
from app.services.license import LicenseService
from app.services.lineage import LineageService
from app.services.recommend import RecommendService

MAX_FACTS_PER_CALL = 12
MAX_CHARS_FOR_LLM = 3000


@dataclass
class Fact:
    """Something the answer may rely on."""

    statement: str
    evidence: Evidence
    source: str


@dataclass
class ToolOutput:
    """What one tool call produced."""

    content: str  # compact JSON, shown to the LLM
    facts: list[Fact] = field(default_factory=list)
    success: bool = True
    cypher: CypherOutcome | None = None


def _summary(model) -> str:
    parts = [model.id]
    if model.task:
        parts.append(f"task {model.task}")
    if model.params_b:
        parts.append(f"{model.params_b:g}B params")
    if model.downloads is not None:
        parts.append(f"{model.downloads:,} downloads")
    parts.append(f"license {model.license or 'not declared'}")
    return ", ".join(parts)


def _spec(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


MODEL_ID = {"model_id": {"type": "string", "description": 'Exact Hugging Face id, e.g. "mistralai/Mistral-7B-v0.1"'}}

TOOL_SPECS = [
    _spec(
        "search_models",
        "Find models by name or organization (fulltext). Use it first when the user gives an approximate name.",
        {"query": {"type": "string"}, "task": {"type": "string", "description": "optional pipeline tag"}},
        ["query"],
    ),
    _spec(
        "model_details",
        "Card facts of one model: publisher, license, datasets, papers, parents.",
        MODEL_ID,
        ["model_id"],
    ),
    _spec("lineage", "Family tree above a model: every path up to its foundation model(s).", MODEL_ID, ["model_id"]),
    _spec(
        "license_check",
        "Can a company use this model commercially? Looks at the licenses of every ancestor and the training data.",
        MODEL_ID,
        ["model_id"],
    ),
    _spec(
        "recommend",
        "Shortlist of models for a client brief.",
        {
            "task": {"type": "string", "description": 'pipeline tag, e.g. "text-generation"'},
            "max_params_b": {"type": "number", "description": "size limit in billions"},
            "language": {"type": "string", "description": 'ISO code, e.g. "fr"'},
            "commercial_use": {"type": "boolean"},
        },
        ["task"],
    ),
    _spec("similar_models", "Models trained on the same datasets or citing the same papers.", MODEL_ID, ["model_id"]),
    _spec(
        "predicted_parents",
        "Probable parent of a model whose card declares none (machine-learning guess, HYPOTHESIS).",
        MODEL_ID,
        ["model_id"],
    ),
    _spec(
        "influential_models",
        "Base models the ecosystem builds on the most (PageRank on the lineage).",
        {"task": {"type": "string", "description": "optional pipeline tag"}},
        [],
    ),
    _spec(
        "text_to_cypher",
        (
            "Any other question about the graph (counts, rankings, comparisons): writes and runs a validated "
            "read-only Cypher query. Pass the question in plain English."
        ),
        {"question": {"type": "string"}},
        ["question"],
    ),
]
TOOL_NAMES = [spec["function"]["name"] for spec in TOOL_SPECS]


class AgentTools:
    """Runs a tool by name; never raises (errors become a failed ToolOutput)."""

    def __init__(self, repo: GraphRepository, db: Neo4jClient, settings: Settings, llm: ChatModel):
        self.repo = repo
        self.settings = settings
        self.cypher = TextToCypher(llm, db, max_depth=settings.max_traversal_depth)
        self._handlers: dict[str, Callable[..., ToolOutput]] = {
            "search_models": self.search_models,
            "model_details": self.model_details,
            "lineage": self.lineage,
            "license_check": self.license_check,
            "recommend": self.recommend,
            "similar_models": self.similar_models,
            "predicted_parents": self.predicted_parents,
            "influential_models": self.influential_models,
            "text_to_cypher": self.text_to_cypher,
        }

    def run(self, name: str, arguments: dict) -> ToolOutput:
        """Call one tool with the LLM's arguments."""
        handler = self._handlers.get(name)
        if handler is None:
            return ToolOutput(json.dumps({"error": f"unknown tool {name}, use one of {TOOL_NAMES}"}), success=False)
        try:
            output = handler(**arguments)
        except TypeError as exc:  # missing or unexpected argument from the LLM
            return ToolOutput(json.dumps({"error": f"bad arguments for {name}: {exc}"}), success=False)
        except AppError as exc:  # not found, GDS not run... : the LLM should know and adapt
            return ToolOutput(json.dumps({"error": exc.detail}), success=False)
        output.facts = output.facts[:MAX_FACTS_PER_CALL]
        output.content = output.content[:MAX_CHARS_FOR_LLM]
        return output

    # ------------------------------------------------------------ tools

    def search_models(self, query: str, task: str | None = None) -> ToolOutput:
        """Fulltext search."""
        response = CatalogService(self.repo).search(query, task or None, 5)
        facts = [Fact(_summary(hit.model), Evidence.VERIFIED, "search_models") for hit in response.hits]
        return ToolOutput(json.dumps([hit.model.model_dump(exclude_none=True) for hit in response.hits]), facts)

    def model_details(self, model_id: str) -> ToolOutput:
        """Model page."""
        detail = CatalogService(self.repo).detail(model_id)
        facts = [Fact(_summary(detail.model), Evidence.VERIFIED, "model_details")]
        if detail.organization:
            facts.append(Fact(f"{model_id} is published by {detail.organization}", Evidence.VERIFIED, "model_details"))
        for parent in detail.parents:
            facts.append(
                Fact(f"{model_id} is a {parent.relation.value} of {parent.id}", Evidence.VERIFIED, "model_details")
            )
        if detail.datasets:
            facts.append(
                Fact(
                    f"{model_id} declares training data: {', '.join(detail.datasets[:8])}",
                    Evidence.VERIFIED,
                    "model_details",
                )
            )
        if detail.descendant_count is not None:
            facts.append(
                Fact(f"{model_id} has {detail.descendant_count:,} derived models", Evidence.DERIVED, "model_details")
            )
        return ToolOutput(detail.model_dump_json(exclude_none=True, exclude={"evidence"}), facts)

    def lineage(self, model_id: str) -> ToolOutput:
        """Paths up to the roots."""
        response = LineageService(self.repo, self.settings).lineage(model_id, self.settings.max_traversal_depth)
        facts = []
        for path in response.paths[:5]:
            chain = path.nodes[0].id
            for node, relation in zip(path.nodes[1:], path.relations):
                chain += f" -[{relation.value}]-> {node.id}"
            facts.append(Fact(f"lineage path: {chain}", Evidence.VERIFIED, "lineage"))
        if not response.paths:
            facts.append(Fact(f"{model_id} declares no parent (it is a root)", Evidence.VERIFIED, "lineage"))
        content = {"roots": response.roots, "truncated": response.truncated, "paths": [f.statement for f in facts]}
        return ToolOutput(json.dumps(content), facts)

    def license_check(self, model_id: str) -> ToolOutput:
        """Commercial-use verdict."""
        check = LicenseService(self.repo, self.settings).check(model_id)
        facts = [
            Fact(
                f"{model_id} declares license {check.declared_license.id if check.declared_license else 'none'}",
                Evidence.VERIFIED,
                "license_check",
            ),
            Fact(
                f"license verdict for {model_id}: {check.verdict.value} ({check.explanation})",
                Evidence.DERIVED,
                "license_check",
            ),
        ]
        for conflict in check.conflicts[:4]:
            facts.append(Fact(f"license conflict: {conflict.reason}", Evidence.DERIVED, "license_check"))
        content = {
            "verdict": check.verdict.value,
            "explanation": check.explanation,
            "conflicts": [c.reason for c in check.conflicts[:4]],
            "incomplete_lineage": check.incomplete_lineage,
            "disclaimer": check.disclaimer,
        }
        return ToolOutput(json.dumps(content), facts)

    def recommend(
        self,
        task: str,
        max_params_b: float | None = None,
        language: str | None = None,
        commercial_use: bool = False,
        **_ignored,
    ) -> ToolOutput:
        """Shortlist for a brief."""
        brief = RecommendRequest(
            task=task, max_params_b=max_params_b or None, language=language or None, commercial_use=bool(commercial_use)
        )
        response = RecommendService(self.repo, self.settings).recommend(brief)
        facts = []
        for rec in response.results:
            verdict = f", license verdict {rec.license_verdict.value}" if rec.license_verdict else ""
            facts.append(Fact(f"recommended: {_summary(rec.model)}{verdict}", Evidence.DERIVED, "recommend"))
        return ToolOutput(
            json.dumps({"results": [f.statement for f in facts], "considered": response.considered}), facts
        )

    def similar_models(self, model_id: str) -> ToolOutput:
        """Node Similarity neighbours."""
        response = AnalyticsService(self.repo).similar(model_id, 5)
        facts = [
            Fact(
                f"{model_id} is similar to {s.model.id} (Jaccard {s.score}, {s.shared_datasets} shared datasets)",
                Evidence.DERIVED,
                "similar_models",
            )
            for s in response.results
        ]
        return ToolOutput(json.dumps({"similar": [f.statement for f in facts], "note": response.note}), facts)

    def predicted_parents(self, model_id: str) -> ToolOutput:
        """Link prediction results."""
        response = AnalyticsService(self.repo).predicted_parents(model_id, 3)
        facts = [
            Fact(
                f"{model_id} is probably derived from {p.model.id} (ML guess, score {p.score:.2f})",
                Evidence.HYPOTHESIS,
                "predicted_parents",
            )
            for p in response.results
        ]
        content = {"predictions": [f.statement for f in facts], "note": response.note}
        return ToolOutput(json.dumps(content), facts)

    def influential_models(self, task: str | None = None) -> ToolOutput:
        """PageRank top models."""
        response = AnalyticsService(self.repo).influential(task or None, 5)
        facts = [
            Fact(
                f"influential: {r.model.id} (PageRank {r.pagerank}, {r.descendant_count} derived models)",
                Evidence.DERIVED,
                "influential_models",
            )
            for r in response.results
        ]
        return ToolOutput(json.dumps({"results": [f.statement for f in facts]}), facts)

    def text_to_cypher(self, question: str) -> ToolOutput:
        """Generated, validated, read-only Cypher."""
        outcome = self.cypher.run(question)
        if not outcome.succeeded:
            errors = outcome.attempts[-1].errors if outcome.attempts else ["no query"]
            content = json.dumps({"error": "no valid query after repairs", "last_errors": errors})
            return ToolOutput(content, success=False, cypher=outcome)
        rows = outcome.rows[:MAX_FACTS_PER_CALL]
        facts = [
            Fact(f"query result: {json.dumps(row, default=str)}", Evidence.DERIVED, "text_to_cypher") for row in rows
        ]
        if not rows:
            facts = [Fact(f"the query returned no row for: {question}", Evidence.DERIVED, "text_to_cypher")]
        return ToolOutput(json.dumps({"rows": rows}, default=str), facts, cypher=outcome)
