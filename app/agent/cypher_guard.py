"""Static checks on LLM-generated Cypher, before it ever reaches Neo4j.

This is the first of three layers (the next two are EXPLAIN and a READ-only
session with a timeout, see text_to_cypher.py). Rules:

1. one statement, no comments (comments can hide things from a reviewer);
2. read-only: no write or admin keyword, no procedure CALL (db.*, apoc.*, gds.*);
3. schema allowlist: every label, relationship type and property must exist;
4. every variable-length pattern has an upper bound <= MAX_TRAVERSAL_DEPTH;
5. a LIMIT is always there, and never above the cap (we add or lower it).

Keywords are checked on a copy where string literals are blanked, so
`WHERE m.id = 'delete-me'` is fine but `DETACH DELETE m` is not.
"""

import re
from dataclasses import dataclass, field

from app.agent.schema import ALLOWED_LABELS, ALLOWED_PROPERTIES, ALLOWED_REL_TYPES

DEFAULT_LIMIT = 25
MAX_LIMIT = 100

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_WRITE = re.compile(
    (
        r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|FOREACH|LOAD\s+CSV|IN\s+TRANSACTIONS|"
        r"GRANT|REVOKE|DENY|ALTER|RENAME|SHOW|TERMINATE|USE|UNION)\b"
    ),
    re.IGNORECASE,
)
_PROCEDURE = re.compile(r"\bCALL\s+(?![({])", re.IGNORECASE)
_NAMESPACE = re.compile(r"\b(apoc|gds|db|dbms|genai)\s*\.", re.IGNORECASE)
_START = re.compile(r"^\s*(MATCH|OPTIONAL\s+MATCH|WITH|UNWIND|RETURN|CALL\s*[({])", re.IGNORECASE)
_RETURN = re.compile(r"\bRETURN\b", re.IGNORECASE)
_MAP = re.compile(r"\{[^{}()]*\}")  # map literals / projections, but not CALL { ... } subqueries
_LABEL = re.compile(r":\s*`?([A-Za-z_][A-Za-z0-9_]*)`?")
_REL_BLOCK = re.compile(r"\[([^\[\]]*)\]")
_VAR_LENGTH = re.compile(r"\*\s*(\d*)\s*(\.\.\s*(\d*))?")
_PROPERTY = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)")
_LIMIT = re.compile(r"\bLIMIT\s+(\d+)\s*$", re.IGNORECASE)


@dataclass
class GuardResult:
    """Outcome of the static check. `cypher` is the (possibly LIMIT-fixed) query to run."""

    cypher: str
    errors: list[str] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """True when the query can go on to EXPLAIN."""
        return not self.errors


def _mask_strings(cypher: str) -> str:
    """Blank string literals and odd `quoted names`, in one left-to-right scan.

    One scan (not two regexes) so a quote inside backticks can't open a fake string
    that hides the rest of the query: MATCH (`x'`:Model) DETACH DELETE `x'` (found by
    Ylias in the review of #13). A backtick name that is a plain identifier keeps its
    text, so `Model` is still checked against the schema.
    """
    out, i = [], 0
    while i < len(cypher):
        char = cypher[i]
        if char not in "'\"`":
            out.append(char)
            i += 1
            continue
        j = i + 1
        while j < len(cypher) and cypher[j] != char:
            j += 2 if cypher[j] == "\\" and char != "`" else 1
        inside = cypher[i + 1 : j]
        if char == "`":
            out.append(inside if _IDENTIFIER.fullmatch(inside) else "quoted_name")
        else:
            out.append("''")
        i = j + 1
    return "".join(out)


def _strip_fences(cypher: str) -> str:
    """LLMs like to wrap code in ```cypher fences."""
    text = cypher.strip()
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip().rstrip(";").strip()


def _schema_errors(masked: str) -> list[str]:
    errors = []
    no_maps = masked
    while _MAP.search(no_maps):  # nested maps: strip from the inside out
        no_maps = _MAP.sub(" ", no_maps)
    rel_types = set()
    for block in _REL_BLOCK.findall(no_maps):
        if ":" in block:
            types = block.split(":", 1)[1]
            types = re.split(r"[*{]", types)[0]
            rel_types.update(t.strip(" :`!") for t in types.split("|") if t.strip(" :`!"))
    labels = set(_LABEL.findall(_REL_BLOCK.sub(" ", no_maps)))
    for name in sorted(labels - ALLOWED_LABELS):
        errors.append(f"unknown label :{name} (allowed: {', '.join(sorted(ALLOWED_LABELS))})")
    for name in sorted(rel_types - ALLOWED_REL_TYPES):
        errors.append(f"unknown relationship type :{name} (allowed: {', '.join(sorted(ALLOWED_REL_TYPES))})")
    for name in sorted(set(_PROPERTY.findall(no_maps)) - ALLOWED_PROPERTIES):
        hint = " (Unlisted is a label: write WHERE NOT m:Unlisted)" if name.lower() == "unlisted" else ""
        errors.append(f"unknown property .{name}{hint}")
    return errors


def _depth_errors(masked: str, max_depth: int) -> list[str]:
    errors = []
    for block in _REL_BLOCK.findall(masked):
        for match in _VAR_LENGTH.finditer(block):
            low, has_range, high = match.group(1), match.group(2), match.group(3)
            upper = int(high) if has_range and high else (int(low) if low and not has_range else None)
            if upper is None:
                errors.append(f"variable-length pattern [{block.strip()}] needs an upper bound (e.g. *1..{max_depth})")
            elif upper > max_depth:
                errors.append(f"variable-length pattern goes up to {upper} hops, the maximum is {max_depth}")
    return errors


def check_cypher(cypher: str, max_depth: int = 6, max_limit: int = MAX_LIMIT) -> GuardResult:
    """Run every static rule; return the query to execute and the list of problems."""
    text = _strip_fences(cypher)
    result = GuardResult(cypher=text)
    if not text:
        result.errors.append("empty query")
        return result
    masked = _mask_strings(text)
    if ";" in masked:
        result.errors.append("only one statement is allowed")
    if "//" in masked or "/*" in masked:
        result.errors.append("comments are not allowed")
    for keyword in sorted({m.group(1).upper() for m in _WRITE.finditer(masked)}):
        result.errors.append(f"forbidden keyword {keyword}: the graph is read-only")
    if _PROCEDURE.search(masked) or _NAMESPACE.search(masked):
        result.errors.append("procedure calls (db.*, apoc.*, gds.*) are not allowed")
    if not _START.search(masked):
        result.errors.append("the query must start with MATCH, OPTIONAL MATCH, WITH, UNWIND or RETURN")
    if not _RETURN.search(masked):
        result.errors.append("the query must RETURN something")
    result.errors += _schema_errors(masked)
    result.errors += _depth_errors(masked, max_depth)
    if result.errors:
        return result

    limit = _LIMIT.search(masked)
    if limit is None:
        result.cypher = f"{text}\nLIMIT {DEFAULT_LIMIT}"
        result.fixes.append(f"added LIMIT {DEFAULT_LIMIT}")
    elif int(limit.group(1)) > max_limit:
        result.cypher = text[: limit.start(1)] + str(max_limit)
        result.fixes.append(f"lowered LIMIT {limit.group(1)} to {max_limit}")
    return result
