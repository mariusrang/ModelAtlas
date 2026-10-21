"""First guardrail: screen the question before any LLM call.

Cheap regex rules for the obvious cases (write orders, prompt injection, secrets).
They don't need to be perfect: the Cypher guard and the read-only session stop writes
anyway. Here we just refuse early with a clear reason.

The rules look for orders, not for single words: "which models drop the license of
their parent?" or "remove all doubt: is Llama 3 commercial?" contain a write verb but
are normal questions. A sentence is a write order when it starts with a write verb
(after an optional "please", "could you kindly", "peux-tu"...) and names something of
the graph (models, licenses, relationships...). The questions we checked, refused and
allowed, are in tests/unit/test_input_guard.py.
"""

import re

_POLITE = (
    r"(?:please[\s,]+|kindly\s+|(?:can|could|would|will)\s+you[\s,]+(?:please[\s,]+|kindly\s+)?|i\s+want\s+you\s+to\s+|"
    r"(?:peux|pourrais)[- ]tu\s+|(?:pouvez|pourriez)[- ]vous\s+|merci\s+de\s+|veuillez\s+)*"
)
_WRITE_VERBS = (
    r"delete|drop|wipe(?:\s+out)?|erase|truncate|destroy|overwrite|create|insert|update|merge|add|set|modify|"
    r"change|rename|remove|replace|get\s+rid\s+of|purge|clear(?:\s+out)?|"
    r"supprim\w*|efface\w*|vide[rz]?|d[ée]trui\w*|[ée]cras\w*|cr[ée]{1,2}[rz]?|ajoute[rz]?|modifie[rz]?|"
    r"change[rz]?|renomme[rz]?|retire[rz]?|enl[eè]ve[rz]?|remplace[rz]?|mets|mettre"
)
_ORDER = re.compile(rf"^\W*{_POLITE}(?P<verb>{_WRITE_VERBS})\b(?P<rest>.*)$", re.IGNORECASE | re.DOTALL)
# the same verbs used with another meaning: "add up the downloads", "change in license between..."
_NOT_AN_ORDER = re.compile(
    r"^(add\s+up|change\s+(in|of|between|from)|set\s+of|update\s+(on|of|about)|drop\s+(in|of)|merge\s+(of|between))\b",
    re.IGNORECASE,
)
_GRAPH_OBJECT = re.compile(
    (
        r"\b(models?|nodes?|n[oœ]euds?|graph\w*|database|base|data|donn[ée]es|relation\w*|edges?|links?|liens?|"
        r"licen[sc]es?|datasets?|mod[eè]les?|everything|tout|labels?|propert\w*|propri[ée]t[ée]s?|"
        r"organi[sz]ations?|papers?|index\w*|constraints?)\b|\w+/\w+|(?-i:\b[A-Z]+_[A-Z_]+\b)"
    ),
    re.IGNORECASE,
)
_RAW_WRITE_CYPHER = re.compile(
    r"\b(DETACH\s+DELETE|CREATE\s*\(|MERGE\s*\(|SET\s+\w+\.\w+\s*=|DROP\s+(INDEX|CONSTRAINT))"
)
_INJECTION = re.compile(
    (
        r"\b(ignore|forget|disregard|oublie\w*|ignore[rz]?)\b.{0,40}"
        r"\b(instructions?|rules?|prompt|r[eè]gles?|consignes?)"
        r"|\b(system prompt|prompt syst[eè]me|you are now|tu es maintenant|jailbreak|developer mode)\b"
        r"|(^|[.!?]\s*|\b(you|now|please|tu)\s+)(act|pretend)\s+(as|to be|that)\b"
        r"|(?-i:\bDAN\b)\s+(mode|prompt)|\b(as|are|be)\s+(?-i:DAN)\b"
    ),
    re.IGNORECASE,
)
# names that are always about our configuration
_SECRET_NAMES = re.compile(
    (
        r"\b(pass\s*-?\s*words?|mot\s+de\s+passe|api[ _-]?keys?|access\s+tokens?|secret\s+keys?|credentials?|"
        r"NEO4J_PASSWORD|ADMIN_API_KEY)\b|\.env\b"
    ),
    re.IGNORECASE,
)
# "token" and "secret" are normal words here ("128k token context", "the secret behind Qwen's success"),
# so they only count next to something of the server
_SERVER = r"\b(server|serveur|neo4j|database|config\w*|admin|system|stored)\b"
_SECRET_WORDS = re.compile(
    rf"\b(tokens?|secrets?)\b.{{0,40}}{_SERVER}|{_SERVER}.{{0,40}}\b(tokens?|secrets?)\b", re.IGNORECASE
)


def _sentences(question: str) -> list[str]:
    return [s.strip() for s in re.split(r"[.!?;:\n]+", question) if s.strip()]


def is_write_order(question: str) -> bool:
    """True when a sentence of the question orders a change to the graph."""
    if _RAW_WRITE_CYPHER.search(question):
        return True
    for sentence in _sentences(question):
        order = _ORDER.match(sentence)
        if not order or _NOT_AN_ORDER.match(sentence[order.start("verb") :]):
            continue
        if _GRAPH_OBJECT.search(order.group("rest")):
            return True
    return False


def screen_question(question: str) -> str | None:
    """Return why the question is refused, or None when it can go to the agent."""
    if is_write_order(question):
        return "write_request: ModelAtlas is read-only, it can't create, change or delete anything in the graph"
    if _INJECTION.search(question):
        return "prompt_injection: the question tries to change the assistant's instructions"
    if _SECRET_NAMES.search(question) or _SECRET_WORDS.search(question):
        return "secrets: the assistant never reveals passwords, keys or configuration"
    return None
