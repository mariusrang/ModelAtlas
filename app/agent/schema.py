"""What the LLM is allowed to know and use about the graph.

The same lists feed the prompt (so the model knows the schema) and the Cypher
guard (so anything outside the schema is rejected before reaching Neo4j).
"""

from app.domain import DERIVED_REL_TYPES, HYPOTHESIS_REL_TYPES, LINEAGE_TYPES, NODE_LABELS, OTHER_REL_TYPES

NODE_PROPERTIES = {
    "Model": [
        "id",
        "author",
        "downloads",
        "likes",
        "library",
        "task",
        "created_at",
        "last_modified",
        "params_b",
        "params_estimated",
        "card_length",
        "card_is_template",
        "formats",
        "lineage_depth",
        "child_count",
        "descendant_count",
        "root_ids",
        "pagerank",
        "family_id",
        "community",
    ],
    "Organization": ["id"],
    "License": ["id", "family", "commercial_use", "requires_attribution", "share_alike", "url", "notes"],
    "Dataset": ["id", "author", "downloads", "likes", "created_at", "size_category", "task_categories", "listed"],
    "Paper": ["arxiv_id"],
    "Task": ["id"],
    "Language": ["code"],
    "Architecture": ["id"],
}
REL_PROPERTIES = {"SIMILAR_TO": ["score"], "PREDICTED_PARENT": ["score", "rank", "model_version"]}

ALLOWED_LABELS = frozenset(NODE_LABELS)
ALLOWED_REL_TYPES = frozenset((*LINEAGE_TYPES, *OTHER_REL_TYPES, *DERIVED_REL_TYPES, *HYPOTHESIS_REL_TYPES))
ALLOWED_PROPERTIES = frozenset(
    [p for props in NODE_PROPERTIES.values() for p in props]
    + [p for props in REL_PROPERTIES.values() for p in props]
    + ["declared", "source"]
    # accessors on temporal values, e.g. m.created_at.year
    + ["year", "month", "day", "epochSeconds", "epochMillis"]
)


def _props(label: str) -> str:
    return ", ".join(NODE_PROPERTIES[label])


SCHEMA_TEXT = f"""Neo4j graph of the Hugging Face model ecosystem.

Nodes (label: properties)
- Model: {_props("Model")}. id looks like "mistralai/Mistral-7B-v0.1". task is a Hugging Face pipeline tag
  ("text-generation", "text-classification"...). params_b = size in billions. downloads/likes are counts.
- Model:Unlisted = a model cited as a parent that has no card of its own (few properties).
- Organization: {_props("Organization")}   License: {_props("License")}   (family: permissive, copyleft,
  non-commercial, restricted, unknown; commercial_use: true, false or "conditional")
- Dataset: {_props("Dataset")}   Paper: {_props("Paper")}   Task: {_props("Task")}
- Language: {_props("Language")}   Architecture: {_props("Architecture")}

Relationships (always in this direction)
- (child:Model)-[:FINETUNED_FROM|ADAPTER_OF|QUANTIZED_FROM|MERGED_FROM]->(parent:Model)   lineage, declared in cards
- (:Model)-[:PUBLISHED_BY]->(:Organization)
- (:Model)-[:HAS_LICENSE]->(:License), (:Dataset)-[:HAS_LICENSE]->(:License)
- (:Model)-[:TRAINED_ON]->(:Dataset)
- (:Model)-[:CITES]->(:Paper)
- (:Model)-[:PERFORMS]->(:Task)
- (:Model)-[:SUPPORTS]->(:Language), (:Dataset)-[:SUPPORTS]->(:Language)
- (:Model)-[:HAS_ARCHITECTURE]->(:Architecture)
- (:Model)-[:SIMILAR_TO {{score}}]-(:Model)        computed by Node Similarity (DERIVED)
- (:Model)-[:PREDICTED_PARENT {{score, rank}}]->(:Model)   guessed by our ML model (HYPOTHESIS)
"""
