"""Graph vocabulary shared by the ETL, the API and the agent.

Keeping relationship names in one place means a typo can't silently create a
new relationship type, and the agent's Cypher validator can use the same list.
"""

from enum import Enum

# Lineage relationships, stored child -> parent.
LINEAGE_TYPES = ("FINETUNED_FROM", "ADAPTER_OF", "QUANTIZED_FROM", "MERGED_FROM")
# Ready-to-use pattern fragment: (m)-[:FINETUNED_FROM|ADAPTER_OF|...]->(p)
LINEAGE_PATTERN = "|".join(LINEAGE_TYPES)

NODE_LABELS = ("Model", "Unlisted", "Organization", "License", "Dataset", "Paper", "Task", "Language", "Architecture")
OTHER_REL_TYPES = (
    "PUBLISHED_BY",
    "HAS_LICENSE",
    "TRAINED_ON",
    "CITES",
    "PERFORMS",
    "SUPPORTS",
    "HAS_ARCHITECTURE",
)


class Evidence(str, Enum):
    """How much you can trust a piece of information returned by the API."""

    VERIFIED = "VERIFIED"  # declared in a Hugging Face model card / registry
    DERIVED = "DERIVED"  # computed by one of our rules from verified facts
    HYPOTHESIS = "HYPOTHESIS"  # guessed by our ML model or the LLM (always with a confidence)


class Relation(str, Enum):
    """Lineage relation kinds as exposed by the API (lower-case, Hub vocabulary)."""

    FINETUNE = "finetune"
    ADAPTER = "adapter"
    QUANTIZED = "quantized"
    MERGE = "merge"

    @property
    def rel_type(self) -> str:
        """Neo4j relationship type for this relation kind."""
        return {
            "finetune": "FINETUNED_FROM",
            "adapter": "ADAPTER_OF",
            "quantized": "QUANTIZED_FROM",
            "merge": "MERGED_FROM",
        }[self.value]


def relation_from_type(rel_type: str) -> str:
    """FINETUNED_FROM -> 'finetune' (for API responses)."""
    return {r.rel_type: r.value for r in Relation}[rel_type]
