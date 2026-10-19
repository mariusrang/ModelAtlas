"""First guardrail: screen the question before any LLM call.

Cheap regex rules for the obvious cases (destructive requests, prompt injection,
secrets). They don't need to be perfect: the Cypher guard and the read-only
session stop writes anyway. Here we just refuse early with a clear reason.
"""

import re

_DESTRUCTIVE = re.compile(
    (
        r"\b(delete|drop|wipe|erase|truncate|destroy|overwrite|"
        r"supprim\w*|efface\w*|vide[rz]?|d[ée]trui\w*|[ée]cras\w*)\b"
        r".{0,60}\b(models?|nodes?|n[oœ]euds?|graph\w*|database|base|data|donn[ée]es|relation\w*|edges?|"
        r"licen[sc]es?|datasets?|mod[eè]les?|everything|tout)\b"
    ),
    re.IGNORECASE,
)
# "create / add / change ..." only count as a write request when they start the sentence (an order),
# so "models created in 2024" or "which licenses changed" are still fine.
_IMPERATIVE_WRITE = re.compile(
    (
        r"^\W*(please\s+|peux[- ]tu\s+|can you\s+)?(create|insert|update|merge|add|set|modify|change|rename|remove|"
        r"cr[ée]e[rz]?|ajoute[rz]?|modifie[rz]?|change[rz]?|renomme[rz]?|retire[rz]?|enl[eè]ve[rz]?)\b"
    ),
    re.IGNORECASE,
)
_RAW_WRITE_CYPHER = re.compile(
    (r"\b(DETACH\s+DELETE|CREATE\s*\(|MERGE\s*\(|SET\s+\w+\.\w+\s*=|DROP\s+(INDEX|CONSTRAINT))"),
)
_INJECTION = re.compile(
    (
        r"(ignore|forget|disregard|oublie|ignore[rz]?)\b.{0,40}\b(instructions?|rules?|prompt|r[eè]gles?|consignes?)"
        r"|\b(system prompt|prompt syst[eè]me|you are now|tu es maintenant|act as|jailbreak|developer mode|DAN)\b"
    ),
    re.IGNORECASE,
)
_SECRETS = re.compile(
    (r"\b(password|mot de passe|api[ _-]?key|secret|token|credentials?|\.env|NEO4J_PASSWORD|ADMIN_API_KEY)\b"),
    re.IGNORECASE,
)


def screen_question(question: str) -> str | None:
    """Return why the question is refused, or None when it can go to the agent."""
    if _RAW_WRITE_CYPHER.search(question) or _DESTRUCTIVE.search(question) or _IMPERATIVE_WRITE.search(question):
        return "write_request: ModelAtlas is read-only, it can't create, change or delete anything in the graph"
    if _INJECTION.search(question):
        return "prompt_injection: the question tries to change the assistant's instructions"
    if _SECRETS.search(question):
        return "secrets: the assistant never reveals passwords, keys or configuration"
    return None
