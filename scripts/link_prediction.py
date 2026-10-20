"""Predict the parent of models that do not declare one (link prediction).

Usage (stack running, graph loaded, `make gds` done):
    python -m scripts.link_prediction            # make predict
    python -m scripts.link_prediction --sample   # small sizes, for CI

How it works:
1. read every listed model and every declared lineage edge from Neo4j;
2. temporal split: we learn on children created before a date and test on
   children created after it, so the test looks like the real use (new models);
3. for each child, candidate parents = older models sharing rare name tokens
   (scripts/ml/names.py); the declared parents among them are the positives;
4. 13 features per (child, candidate) pair (scripts/ml/features.py), then three
   rankers: a name-only baseline, logistic regression, gradient boosting;
5. the best one on validation scores the candidates of "orphan" models (no
   declared parent), and the pairs above a threshold chosen for >= 80% top-1
   precision are written as (:Model)-[:PREDICTED_PARENT {score}]->(:Model).

Predictions are HYPOTHESIS: they live on their own relationship type and are
never mixed with the declared lineage.
"""

import argparse
import logging
import random
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from app.database.neo4j_client import Neo4jClient
from app.domain import LINEAGE_PATTERN
from scripts.etl.load import batches
from scripts.etl.sources import REPORTS_DIR
from scripts.ml.features import FEATURES, ModelTable, normalise, pair_features
from scripts.ml.names import NameIndex
from scripts.seed_data import build_client

logger = logging.getLogger("modelatlas.predict")

MODELS_QUERY = """
MATCH (m:Model) WHERE NOT m:Unlisted
RETURN m.id AS id, m.author AS author, m.created_at.epochSeconds / 86400.0 AS created, m.params_b AS params,
       m.task AS task, coalesce(m.downloads, 0) AS downloads, coalesce(m.likes, 0) AS likes,
       m.content_embedding AS embedding, coalesce(m.child_count, 0) AS child_count,
       COLLECT { MATCH (m)-[:HAS_ARCHITECTURE]->(a:Architecture) RETURN a.id } AS architectures,
       COLLECT { MATCH (m)-[:TRAINED_ON]->(d:Dataset) RETURN d.id } AS datasets,
       COLLECT { MATCH (m)-[:CITES]->(p:Paper) RETURN p.arxiv_id } AS papers
"""
EDGES_QUERY = f"""
MATCH (c:Model)-[:{LINEAGE_PATTERN}]->(p:Model)
WHERE NOT c:Unlisted
RETURN c.id AS child, p.id AS parent, p:Unlisted AS parent_unlisted
"""
CLEAR_QUERY = "MATCH ()-[r:PREDICTED_PARENT]->() CALL (r) { DELETE r } IN TRANSACTIONS OF 10000 ROWS"
WRITE_QUERY = """
UNWIND $rows AS row
MATCH (c:Model {id: row.child})
MATCH (p:Model {id: row.parent})
MERGE (c)-[r:PREDICTED_PARENT]->(p)
SET r.score = row.score, r.rank = row.rank, r.model_version = row.version,
    r.name_score = row.name_score, r.content_cosine = row.content_cosine, r.created_at = datetime()
"""

TARGET_PRECISION = 0.8  # precision we want for the top-1 prediction we write
MIN_ORPHAN_DOWNLOADS = 100  # only predict for models someone actually uses
MAX_CHILDREN_FOR_ORPHAN = 10  # a model with 10+ declared children is a real base model, not a copy
# Plausibility rule on top of the model: a guessed parent must carry at least half of the
# child's name information. Without it, the first run paired unrelated popular models
# (e.g. a BART model with a DeBERTa one) with a score of 1.0 (see the report).
MIN_NAME_SCORE = 0.5
SEED = 42


@dataclass
class Settings:
    """Sizes of one run (smaller for the CI sample)."""

    candidates: int = 50
    max_train: int = 12000
    max_test: int = 4000
    val_share: float = 0.15
    test_share: float = 0.2
    top_k: int = 1  # the threshold is calibrated on the top-1 guess, so we only write that one


@dataclass
class PairSet:
    """Feature rows of (child, candidate) pairs, grouped by child."""

    features: np.ndarray
    labels: np.ndarray
    groups: np.ndarray  # child row of each pair
    candidates: np.ndarray  # candidate row of each pair
    children: int = 0
    covered: int = 0  # children whose true parent is among their candidates

    @property
    def candidate_recall(self) -> float:
        """Share of children whose true parent is among their candidates."""
        return self.covered / self.children if self.children else 0.0


@dataclass
class Evaluation:
    """Ranking quality of one model on one pair set."""

    name: str
    auc: float | None
    hits_at_1: float
    hits_at_5: float
    mrr: float
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------- data


def build_table(rows: list[dict]) -> ModelTable:
    """Cypher rows -> ModelTable (embeddings normalised, missing ones = zeros)."""
    dim = max((len(r["embedding"]) for r in rows if r["embedding"]), default=1)
    embeddings = np.array([r["embedding"] if r["embedding"] else [0.0] * dim for r in rows], dtype=float)
    return ModelTable(
        ids=[r["id"] for r in rows],
        authors=[r["author"] for r in rows],
        created=[r["created"] for r in rows],
        params=[r["params"] for r in rows],
        tasks=[r["task"] for r in rows],
        downloads=[int(r["downloads"]) for r in rows],
        likes=[int(r["likes"]) for r in rows],
        architectures=[frozenset(r["architectures"]) for r in rows],
        datasets=[frozenset(r["datasets"]) for r in rows],
        papers=[frozenset(r["papers"]) for r in rows],
        embeddings=normalise(embeddings),
    )


def declared_parents(table: ModelTable, edges: list[dict]) -> tuple[dict[int, set[int]], set[int]]:
    """Parents (listed only) of each child row, and every row that declares any parent."""
    parents: defaultdict = defaultdict(set)
    has_parent = set()
    for edge in edges:
        child = table.row.get(edge["child"])
        if child is None:
            continue
        has_parent.add(child)
        parent = table.row.get(edge["parent"])
        if parent is not None and not edge["parent_unlisted"]:
            parents[child].add(parent)
    return dict(parents), has_parent


def split_children(table: ModelTable, parents: dict[int, set[int]], cfg: Settings) -> dict[str, list[int]]:
    """Temporal split of the children that have a listed parent: train < val < test in time."""
    dated = sorted((c for c in parents if table.created[c] is not None), key=lambda c: table.created[c])
    n_test = int(len(dated) * cfg.test_share)
    n_val = int(len(dated) * cfg.val_share)
    train, val, test = dated[: -n_test - n_val or None], dated[-n_test - n_val : -n_test or None], dated[-n_test:]
    rng = random.Random(SEED)
    return {
        "train": sorted(rng.sample(train, min(len(train), cfg.max_train))),
        "val": sorted(rng.sample(val, min(len(val), cfg.max_test))),
        "test": sorted(rng.sample(test, min(len(test), cfg.max_test))) if n_test else [],
    }


def older_than(table: ModelTable, child: int):
    """Candidate filter: a parent can't be created after its child."""
    child_day = table.created[child]
    return lambda row: child_day is None or table.created[row] is None or table.created[row] <= child_day


def build_pairs(table: ModelTable, names: NameIndex, children: list[int], parents: dict, cfg: Settings) -> PairSet:
    """Candidates + features + labels for a list of children."""
    rows, labels, groups, cands = [], [], [], []
    with_parent = 0
    for child in children:
        candidates = names.candidates(child, older_than(table, child), cfg.candidates)
        truth = parents.get(child, set())
        with_parent += bool(truth & set(candidates))
        for cand in candidates:
            rows.append(pair_features(table, names, child, cand))
            labels.append(int(cand in truth))
            groups.append(child)
            cands.append(cand)
    return PairSet(
        features=np.array(rows, dtype=float).reshape(-1, len(FEATURES)),
        labels=np.array(labels, dtype=int),
        groups=np.array(groups, dtype=int),
        candidates=np.array(cands, dtype=int),
        children=len(children),
        covered=with_parent,
    )


# ---------------------------------------------------------------- models


def baseline_scores(features: np.ndarray) -> np.ndarray:
    """Name-only ranking: name score first, popularity to break ties."""
    return features[:, FEATURES.index("name_score")] + 0.001 * features[:, FEATURES.index("candidate_log_downloads")]


def make_models() -> dict:
    """The two learned rankers we compare to the baseline."""
    return {
        "logistic_regression": make_pipeline(
            StandardScaler(), LogisticRegression(max_iter=2000, class_weight="balanced")
        ),
        "gradient_boosting": HistGradientBoostingClassifier(max_iter=200, learning_rate=0.1, random_state=SEED),
    }


def ranking_metrics(scores: np.ndarray, pairs: PairSet) -> tuple[float, float, float]:
    """Hits@1, Hits@5 and MRR over children (a child whose parent is not a candidate counts as a miss)."""
    by_child: defaultdict = defaultdict(list)
    for score, label, child in zip(scores, pairs.labels, pairs.groups):
        by_child[child].append((score, label))
    hits1 = hits5 = rr = 0.0
    for ranked in by_child.values():
        ranked.sort(key=lambda x: -x[0])
        position = next((i for i, (_, label) in enumerate(ranked) if label), None)
        if position is not None:
            hits1 += position == 0
            hits5 += position < 5
            rr += 1 / (position + 1)
    n = max(pairs.children, 1)
    return hits1 / n, hits5 / n, rr / n


def evaluate(name: str, scores: np.ndarray, pairs: PairSet) -> Evaluation:
    """AUC on pairs + ranking metrics on children."""
    auc = roc_auc_score(pairs.labels, scores) if len(set(pairs.labels)) == 2 else None
    hits1, hits5, mrr = ranking_metrics(scores, pairs)
    return Evaluation(name, auc, hits1, hits5, mrr)


def top1(scores: np.ndarray, pairs: PairSet) -> list[tuple[float, bool]]:
    """(best score, is it a real parent) for every child."""
    best: dict = {}
    for score, label, child in zip(scores, pairs.labels, pairs.groups):
        if child not in best or score > best[child][0]:
            best[child] = (float(score), bool(label))
    return list(best.values())


def choose_threshold(
    predictions: list[tuple[float, bool]], target: float = TARGET_PRECISION, minimum: int = 20
) -> float | None:
    """Lowest score threshold (>= 0.5) whose top-1 precision is still >= target.

    Returns None when no threshold reaches the target: then we write nothing rather
    than predictions we know are worse than promised. Ties are kept together: a
    threshold keeps every prediction with a score >= it.
    """
    chosen = None
    kept = correct = 0
    for score in sorted({score for score, _ in predictions}, reverse=True):
        same = [ok for value, ok in predictions if value == score]
        kept, correct = kept + len(same), correct + sum(same)
        if kept >= minimum and correct / kept >= target and score >= 0.5:
            chosen = score  # never below 0.5: we don't write a pair the model finds more likely wrong
    return chosen


def precision_coverage(predictions: list[tuple[float, bool]], threshold: float | None) -> tuple[float | None, float]:
    """Precision of the top-1 predictions above the threshold, and the share of children that get one."""
    if threshold is None:
        return None, 0.0
    kept = [ok for score, ok in predictions if score >= threshold]
    precision = sum(kept) / len(kept) if kept else 0.0
    return precision, len(kept) / len(predictions) if predictions else 0.0


POPULARITY = ["candidate_log_downloads", "candidate_log_likes"]


class WithoutFeatures:
    """A model trained without some columns: used to measure how much popularity helps.

    Downloads and likes are today's numbers, not the ones known when the child was
    created, so we check the result does not rely on them only.
    """

    def __init__(self, model, dropped: list[str]):
        self.model = model
        self.keep = [i for i, name in enumerate(FEATURES) if name not in dropped]

    def fit(self, features: np.ndarray, labels: np.ndarray):
        """Fit on the kept columns."""
        self.model.fit(features[:, self.keep], labels)
        return self

    def predict_proba(self, features: np.ndarray) -> np.ndarray:
        """Predict from the kept columns."""
        return self.model.predict_proba(features[:, self.keep])


def plausible_scores(model, features: np.ndarray) -> np.ndarray:
    """Model probability, set to 0 when the candidate fails the name rule."""
    scores = model.predict_proba(features)[:, 1]
    return np.where(features[:, FEATURES.index("name_score")] >= MIN_NAME_SCORE, scores, 0.0)


# ---------------------------------------------------------------- run


def orphans(table: ModelTable, has_parent: set[int], child_counts: list[int]) -> list[int]:
    """Used models that declare no parent and are not established base models."""
    return [
        row
        for row in range(len(table))
        if row not in has_parent
        and table.downloads[row] >= MIN_ORPHAN_DOWNLOADS
        and child_counts[row] < MAX_CHILDREN_FOR_ORPHAN
        and table.created[row] is not None  # like the training children: we need the date to filter candidates
    ]


# pylint: disable-next=too-many-arguments,too-many-positional-arguments,too-many-locals
def predict_orphans(table, names, model, rows: list[int], threshold: float, cfg: Settings, version: str) -> list[dict]:
    """Top-k candidates above the threshold for every orphan, ready for WRITE_QUERY."""
    pairs = build_pairs(table, names, rows, {}, cfg)
    if pairs.labels.size == 0:
        return []
    scores = plausible_scores(model, pairs.features)
    by_child: defaultdict = defaultdict(list)
    for i, (child, cand, score) in enumerate(zip(pairs.groups, pairs.candidates, scores)):
        by_child[child].append((score, cand, i))
    out = []
    for child, ranked in by_child.items():
        ranked.sort(key=lambda x: -x[0])
        for rank, (score, cand, i) in enumerate(ranked[: cfg.top_k], start=1):
            if score < threshold or score == 0.0:
                break
            out.append(
                {
                    "child": table.ids[child],
                    "parent": table.ids[cand],
                    "score": round(float(score), 4),
                    "rank": rank,
                    "version": version,
                    "name_score": round(float(pairs.features[i, FEATURES.index("name_score")]), 3),
                    "content_cosine": round(float(pairs.features[i, FEATURES.index("content_cosine")]), 3),
                }
            )
    return out


def fmt(value) -> str:
    """Numbers in the report: 3 decimals, '-' for None."""
    return "-" if value is None else f"{value:.3f}" if isinstance(value, float) else str(value)


def render_report(info: dict) -> str:
    """Markdown report of the run."""
    lines = [
        "# Link prediction report",
        "Generated by `make predict` (`scripts/link_prediction.py`). Predictions are HYPOTHESIS.",
        "## Data",
        *[f"- {key}: **{fmt(value)}**" for key, value in info["data"].items()],
        "## Test results (children created after the training period)",
        "| model | AUC (pairs) | Hits@1 | Hits@5 | MRR |",
        "|---|---|---|---|---|",
        *[
            f"| {e.name} | {fmt(e.auc)} | {fmt(e.hits_at_1)} | {fmt(e.hits_at_5)} | {fmt(e.mrr)} |"
            for e in info["test"]
        ],
        "",
        "Hits@k = share of test children whose declared parent is in our top k. A child whose parent is not",
        f"among the candidates counts as a miss, so Hits@k can't exceed the candidate recall "
        f"({fmt(info['data']['candidate recall (test)'])}).",
        "## Chosen model and threshold",
        *[f"- {key}: **{fmt(value)}**" for key, value in info["chosen"].items()],
        "## Feature weights",
        "| feature | weight |",
        "|---|---|",
        *[f"| {name} | {fmt(weight)} |" for name, weight in info["weights"]],
        "## Predictions written",
        *[f"- {key}: **{fmt(value)}**" for key, value in info["written"].items()],
        "",
        "| model without declared parent | predicted parent | score |",
        "|---|---|---|",
        *[f"| {p['child']} | {p['parent']} | {p['score']} |" for p in info["examples"]],
    ]
    return "\n".join(lines) + "\n"


def feature_weights(model, pairs: PairSet) -> list[tuple[str, float]]:
    """Coefficients for the logistic regression, permutation importance for boosting."""
    if hasattr(model, "named_steps"):
        coefs = model.named_steps["logisticregression"].coef_[0]
        return sorted(zip(FEATURES, map(float, coefs)), key=lambda x: -abs(x[1]))
    from sklearn.inspection import permutation_importance  # pylint: disable=import-outside-toplevel

    n = min(len(pairs.labels), 20000)
    result = permutation_importance(
        model, pairs.features[:n], pairs.labels[:n], scoring="roc_auc", n_repeats=3, random_state=SEED
    )
    return sorted(zip(FEATURES, map(float, result.importances_mean)), key=lambda x: -abs(x[1]))


def run(client: Neo4jClient, cfg: Settings) -> dict:  # pylint: disable=too-many-locals
    """Whole pipeline; returns the report info (and writes the predictions)."""
    rows = client.read(MODELS_QUERY)
    table = build_table(rows)
    names = NameIndex(table.ids, table.downloads)
    parents, has_parent = declared_parents(table, client.read(EDGES_QUERY))
    split = split_children(table, parents, cfg)
    pairs = {name: build_pairs(table, names, children, parents, cfg) for name, children in split.items()}
    info = {
        "data": {
            "listed models": len(table),
            "children with a listed parent": len(parents),
            "train / val / test children": f"{len(split['train'])} / {len(split['val'])} / {len(split['test'])}",
            "candidates per child": cfg.candidates,
            "candidate recall (test)": pairs["test"].candidate_recall,
        },
        "test": [],
    }
    if len(set(pairs["train"].labels)) < 2 or pairs["val"].labels.size == 0:
        logger.warning("not enough labelled pairs to train (graph too small?)")
        info.update(chosen={}, weights=[], written={"predictions": 0}, examples=[])
        return info

    info["test"].append(evaluate("name baseline", baseline_scores(pairs["test"].features), pairs["test"]))
    best_name, best_model, best_mrr = None, None, -1.0
    for name, model in make_models().items():
        model.fit(pairs["train"].features, pairs["train"].labels)
        val_mrr = evaluate(name, model.predict_proba(pairs["val"].features)[:, 1], pairs["val"]).mrr
        test_scores = model.predict_proba(pairs["test"].features)[:, 1]
        info["test"].append(evaluate(name, test_scores, pairs["test"]))
        if val_mrr > best_mrr:
            best_name, best_model, best_mrr = name, model, val_mrr

    ablation = WithoutFeatures(make_models()["gradient_boosting"], POPULARITY)
    ablation.fit(pairs["train"].features, pairs["train"].labels)
    info["test"].append(
        evaluate(
            "gradient_boosting without popularity", ablation.predict_proba(pairs["test"].features)[:, 1], pairs["test"]
        )
    )

    threshold = choose_threshold(top1(plausible_scores(best_model, pairs["val"].features), pairs["val"]))
    test_scores = plausible_scores(best_model, pairs["test"].features)
    info["test"].append(evaluate(f"{best_name} + name rule", test_scores, pairs["test"]))
    test_top1 = top1(test_scores, pairs["test"])
    precision, coverage = precision_coverage(test_top1, threshold)
    version = f"{best_name}-{date.today().isoformat()}"
    info["chosen"] = {
        "model (best validation MRR)": best_name,
        "model_version": version,
        "name rule": f"candidate keeps >= {MIN_NAME_SCORE:.0%} of the child's name information",
        "threshold (top-1 precision >= 0.8 on validation)": (
            round(threshold, 3) if threshold is not None else "none reached the target: nothing written"
        ),
        "test top-1 precision above threshold": precision,
        "test children covered above threshold": coverage,
        "edges written per model": "1 (the top-1 guess, the one the threshold was measured on)",
    }
    info["weights"] = feature_weights(best_model, pairs["test"])

    child_counts = [int(r["child_count"]) for r in rows]
    targets = orphans(table, has_parent, child_counts)
    predictions = []
    if threshold is not None:
        predictions = predict_orphans(table, names, best_model, targets, threshold, cfg, version)
    client.write(CLEAR_QUERY)
    for chunk in batches(predictions, 5000):
        client.write(WRITE_QUERY, {"rows": chunk})
    info["written"] = {
        "orphan models scored (no declared parent, >= 100 downloads)": len(targets),
        "orphans with at least one prediction": len({p["child"] for p in predictions}),
        "PREDICTED_PARENT relationships": len(predictions),
    }
    info["examples"] = sorted(
        (p for p in predictions if p["rank"] == 1),
        key=lambda p: -table.downloads[table.row[p["child"]]],
    )[:15]
    return info


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--uri", default="bolt://localhost:7687")
    parser.add_argument("--sample", action="store_true", help="small sizes, writes reports/link_prediction_sample.md")
    args = parser.parse_args(argv)
    cfg = Settings(max_train=1500, max_test=500) if args.sample else Settings()

    client = build_client(args.uri)
    try:
        info = run(client, cfg)
    finally:
        client.close()
    REPORTS_DIR.mkdir(exist_ok=True)
    name = "link_prediction_sample.md" if args.sample else "link_prediction.md"
    (REPORTS_DIR / name).write_text(render_report(info), encoding="utf-8")
    logger.info(f"{info['written'].get('PREDICTED_PARENT relationships', 0)} predictions written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
