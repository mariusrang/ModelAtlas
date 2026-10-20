"""Natural language -> Cypher, with validation and up to 2 repair attempts.

Three layers, each one catching what the previous one missed:
1. static guard (cypher_guard.py): read-only keywords, schema allowlist, bounded depth, LIMIT;
2. EXPLAIN: Neo4j plans the query without running it (syntax errors, unknown functions);
3. execution in a READ session with a timeout (Neo4jClient.read): even a write that
   slipped through 1 and 2 would be refused by the database itself.
When 1 or 2 fails, the error is sent back to the LLM, which gets 2 chances to fix it.
"""

import logging
import re
from dataclasses import dataclass, field

from neo4j.exceptions import Neo4jError

from app.agent.cypher_guard import check_cypher
from app.agent.llm import ChatModel
from app.agent.schema import SCHEMA_TEXT
from app.database.neo4j_client import Neo4jClient

logger = logging.getLogger(__name__)
MAX_REPAIRS = 2

PROMPT = f"""You write ONE read-only Cypher query for Neo4j 5 that answers the user's question.

{SCHEMA_TEXT}
Rules:
- read only: MATCH / OPTIONAL MATCH / WITH / UNWIND / RETURN, never CREATE, MERGE, SET, DELETE, CALL procedures;
- use only the labels, relationship types and properties listed above;
- variable-length patterns need an upper bound, at most 6 hops (e.g. -[:FINETUNED_FROM*1..3]->);
- `Unlisted` is a label, not a property: "listed models" means MATCH (m:Model) WHERE NOT m:Unlisted;
- match model ids exactly ({{id: "org/name"}}) or with toLower(m.id) CONTAINS "...";
- return plain values with clear aliases (m.id AS model, m.downloads AS downloads), never whole nodes;
- end with ORDER BY and LIMIT (at most 25 rows).
Answer with the query only, no explanation."""

EXAMPLES = [
    (
        "Which organization published the most models?",
        (
            "MATCH (m:Model)-[:PUBLISHED_BY]->(o:Organization)\n"
            "RETURN o.id AS organization, count(m) AS models\nORDER BY models DESC\nLIMIT 10"
        ),
    ),
    (
        "How many quantized versions does mistralai/Mistral-7B-v0.1 have?",
        (
            'MATCH (q:Model)-[:QUANTIZED_FROM]->(:Model {id: "mistralai/Mistral-7B-v0.1"})\n'
            "RETURN count(q) AS quantized_versions\nLIMIT 1"
        ),
    ),
    (
        "How many listed models declare the apache-2.0 license?",
        (
            'MATCH (m:Model)-[:HAS_LICENSE]->(:License {id: "apache-2.0"})\n'
            "WHERE NOT m:Unlisted\nRETURN count(m) AS models\nLIMIT 1"
        ),
    ),
]


@dataclass
class CypherAttempt:
    """One generated query and what the checks said about it."""

    cypher: str
    errors: list[str] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)
    stage: str = "guard"  # where it stopped: guard, explain, execute, ok


@dataclass
class CypherOutcome:
    """Result of the whole text-to-Cypher step."""

    attempts: list[CypherAttempt]
    rows: list[dict] | None = None

    @property
    def succeeded(self) -> bool:
        """True when a query passed every check and ran."""
        return self.rows is not None

    @property
    def repairs(self) -> int:
        """How many times the LLM had to fix its query."""
        return max(len(self.attempts) - 1, 0)


def _clean(text: str) -> str:
    match = re.search(r"```(?:cypher)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    return (match.group(1) if match else text).strip()


class TextToCypher:
    """Generate, validate, repair and run a Cypher query."""

    def __init__(self, llm: ChatModel, db: Neo4jClient, max_depth: int = 6):
        self.llm, self.client, self.max_depth = llm, db, max_depth

    def _messages(self, question: str) -> list[dict]:
        messages = [{"role": "system", "content": PROMPT}]
        for example_q, example_cypher in EXAMPLES:
            messages += [{"role": "user", "content": example_q}, {"role": "assistant", "content": example_cypher}]
        return messages + [{"role": "user", "content": question}]

    def _try(self, cypher: str) -> tuple[CypherAttempt, list[dict] | None]:
        guard = check_cypher(cypher, max_depth=self.max_depth)
        attempt = CypherAttempt(cypher=guard.cypher, errors=list(guard.errors), fixes=list(guard.fixes))
        if not guard.passed:
            return attempt, None
        try:
            self.client.read(f"EXPLAIN {guard.cypher}")
        except Neo4jError as exc:
            attempt.stage, attempt.errors = "explain", [exc.message or str(exc)]
            return attempt, None
        try:
            rows = self.client.read(guard.cypher)
        except Neo4jError as exc:  # timeout, or a write refused by the READ session
            attempt.stage, attempt.errors = "execute", [exc.message or str(exc)]
            return attempt, None
        attempt.stage = "ok"
        return attempt, rows

    def run(self, question: str) -> CypherOutcome:
        """Up to 1 + MAX_REPAIRS generations; stops at the first query that runs."""
        messages = self._messages(question)
        attempts: list[CypherAttempt] = []
        for _ in range(1 + MAX_REPAIRS):
            reply = self.llm.chat(messages)
            cypher = _clean(reply.get("content", ""))
            attempt, rows = self._try(cypher)
            attempts.append(attempt)
            if rows is not None:
                return CypherOutcome(attempts, rows)
            logger.info(f"generated Cypher rejected at {attempt.stage}: {attempt.errors}")
            messages += [
                {"role": "assistant", "content": cypher},
                {
                    "role": "user",
                    "content": "This query was rejected: "
                    + "; ".join(attempt.errors)
                    + ". Write a corrected query that follows the rules. Query only.",
                },
            ]
        return CypherOutcome(attempts)
