"""Features of a (child, candidate parent) pair. Every feature is something a
person would also look at: name, publisher, architecture, size, data, age."""

import math
from dataclasses import dataclass

import numpy as np

from scripts.ml.names import NameIndex

FEATURES = [
    "name_score",
    "same_base_name",
    "token_jaccard",
    "same_author",
    "same_architecture",
    "same_task",
    "params_ratio",
    "shared_datasets",
    "shared_papers",
    "candidate_log_downloads",
    "candidate_log_likes",
    "log_days_between",
    "content_cosine",
]


@dataclass
class ModelTable:  # pylint: disable=too-many-instance-attributes
    """Everything we know about the listed models, row-aligned with `ids`."""

    ids: list[str]
    authors: list[str | None]
    created: list[float | None]  # days since epoch
    params: list[float | None]
    tasks: list[str | None]
    downloads: list[int]
    likes: list[int]
    architectures: list[frozenset]
    datasets: list[frozenset]
    papers: list[frozenset]
    embeddings: np.ndarray  # (n, d), rows normalised to unit length (0 if missing)

    def __post_init__(self):
        self.row = {model_id: i for i, model_id in enumerate(self.ids)}

    def __len__(self) -> int:
        return len(self.ids)


def normalise(vectors: np.ndarray) -> np.ndarray:
    """Unit-length rows, so a dot product is a cosine (zero rows stay zero)."""
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return np.divide(vectors, norms, out=np.zeros_like(vectors), where=norms > 0)


def pair_features(table: ModelTable, names: NameIndex, child: int, cand: int) -> list[float]:
    """One feature row, in the order of FEATURES."""
    child_toks, cand_toks = names.token_sets[child], names.token_sets[cand]
    p_child, p_cand = table.params[child], table.params[cand]
    params_ratio = min(p_child, p_cand) / max(p_child, p_cand) if p_child and p_cand else -1.0
    days = None
    if table.created[child] is not None and table.created[cand] is not None:
        days = table.created[child] - table.created[cand]
    return [
        names.name_score(child, cand),
        float(names.bases[child] == names.bases[cand]),
        len(child_toks & cand_toks) / len(child_toks | cand_toks) if child_toks | cand_toks else 0.0,
        float(table.authors[child] is not None and table.authors[child] == table.authors[cand]),
        float(bool(table.architectures[child] & table.architectures[cand])),
        float(table.tasks[child] is not None and table.tasks[child] == table.tasks[cand]),
        params_ratio,
        float(len(table.datasets[child] & table.datasets[cand])),
        float(len(table.papers[child] & table.papers[cand])),
        math.log10(table.downloads[cand] + 1),
        math.log10(table.likes[cand] + 1),
        math.log10(days + 1) if days is not None and days >= 0 else -1.0,
        float(table.embeddings[child] @ table.embeddings[cand]),
    ]
