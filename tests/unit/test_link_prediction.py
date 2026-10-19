"""Link prediction: names, features, metrics and the whole run on a synthetic graph."""

import random

import numpy as np
import pytest

from scripts import link_prediction as lp
from scripts.ml.features import FEATURES, ModelTable, normalise, pair_features
from scripts.ml.names import NameIndex, base_name, tokens

# ---------------------------------------------------------------- names


def test_tokens_drop_the_organization():
    assert tokens("TheBloke/Mistral-7B-Instruct-v0.2-GGUF") == ["mistral", "7b", "instruct", "v0", "2", "gguf"]


def test_base_name_removes_copy_suffixes():
    assert base_name("TheBloke/Mistral-7B-Instruct-v0.2-GGUF") == base_name("mistralai/Mistral-7B-Instruct-v0.2")
    assert base_name("x/gguf") == "gguf"  # never empty


def test_candidates_put_the_same_base_name_first_and_respect_the_filter():
    ids = ["mistralai/Mistral-7B-v0.1", "meta/Llama-7B", "TheBloke/Mistral-7B-v0.1-GGUF", "other/Mistral-7B-v0.1-AWQ"]
    names = NameIndex(ids, [1000, 5000, 10, 10])
    assert names.candidates(2, lambda row: True)[0] == 0 or names.candidates(2, lambda row: True)[0] == 3
    only_original = names.candidates(2, lambda row: row != 3)
    assert only_original[0] == 0
    assert 2 not in only_original  # never its own parent
    assert names.candidates(2, lambda row: False) == []


def test_name_score_is_one_when_every_child_token_is_in_the_candidate():
    names = NameIndex(["a/Foo-Bar", "b/Foo-Bar-Baz", "c/Qux"], [1, 1, 1])
    assert names.name_score(0, 1) == pytest.approx(1.0)
    assert names.name_score(0, 2) == 0.0


# ---------------------------------------------------------------- features


def _table(ids, **overrides) -> ModelTable:
    n = len(ids)
    values = {
        "authors": [i.split("/")[0] for i in ids],
        "created": [float(i) for i in range(n)],
        "params": [7.0] * n,
        "tasks": ["text-generation"] * n,
        "downloads": [100] * n,
        "likes": [1] * n,
        "architectures": [frozenset({"MistralForCausalLM"})] * n,
        "datasets": [frozenset()] * n,
        "papers": [frozenset()] * n,
        "embeddings": normalise(np.ones((n, 4))),
    }
    values.update(overrides)
    return ModelTable(ids=ids, **values)


def test_pair_features_have_one_value_per_feature_name():
    ids = ["mistralai/Mistral-7B", "TheBloke/Mistral-7B-GGUF"]
    table = _table(ids, params=[7.0, None])
    row = pair_features(table, NameIndex(ids, table.downloads), 1, 0)
    assert len(row) == len(FEATURES)
    values = dict(zip(FEATURES, row))
    assert values["same_base_name"] == 1.0
    assert values["same_author"] == 0.0
    assert values["params_ratio"] == -1.0  # unknown size
    assert values["content_cosine"] == pytest.approx(1.0)
    assert values["log_days_between"] == pytest.approx(np.log10(2))


def test_normalise_keeps_zero_rows():
    out = normalise(np.array([[3.0, 4.0], [0.0, 0.0]]))
    assert out[0] == pytest.approx([0.6, 0.8])
    assert not out[1].any()


# ---------------------------------------------------------------- metrics


def _pairs(labels, groups, children=None) -> lp.PairSet:
    return lp.PairSet(
        features=np.zeros((len(labels), len(FEATURES))),
        labels=np.array(labels),
        groups=np.array(groups),
        candidates=np.arange(len(labels)),
        children=children or len(set(groups)),
    )


def test_ranking_metrics():
    # child 0: parent ranked 1st, child 1: parent ranked 2nd, child 2: parent not among the candidates
    pairs = _pairs([1, 0, 0, 1, 0], [0, 0, 1, 1, 2])
    hits1, hits5, mrr = lp.ranking_metrics(np.array([0.9, 0.1, 0.8, 0.5, 0.3]), pairs)
    assert hits1 == pytest.approx(1 / 3)
    assert hits5 == pytest.approx(2 / 3)
    assert mrr == pytest.approx((1 + 0.5) / 3)


def test_threshold_keeps_the_target_precision():
    predictions = [(0.9, True)] * 30 + [(0.6, True)] * 10 + [(0.5, False)] * 40
    threshold = lp.choose_threshold(predictions, target=0.8)
    precision, coverage = lp.precision_coverage(predictions, threshold)
    assert threshold == 0.6
    assert precision == 1.0
    assert coverage == pytest.approx(0.5)


def test_threshold_defaults_to_one_half_when_nothing_is_precise_enough():
    assert lp.choose_threshold([(0.9, False)] * 30) == 0.5


# ---------------------------------------------------------------- whole run


def synthetic_graph(n_bases: int = 60):
    """Base models, their GGUF / fine-tuned copies (declared parents) and look-alike siblings."""
    rng = random.Random(0)
    rows, edges = [], []

    def add(model_id, day, downloads, author=None, child_count=0, params=7.0):
        rows.append(
            {
                "id": model_id,
                "author": author or model_id.split("/")[0],
                "created": float(day),
                "params": params,
                "task": "text-generation",
                "downloads": downloads,
                "likes": downloads // 100,
                "embedding": [rng.random() for _ in range(8)],
                "child_count": child_count,
                "architectures": ["LlamaForCausalLM"],
                "datasets": [],
                "papers": [],
            }
        )

    for i in range(n_bases):
        word = f"model{i}"
        add(f"org{i}/{word}-7b", i, 50000, child_count=3)
        add(f"org{i}/{word}-13b", i + 1, 20000, params=13.0)  # sibling: similar name, not the parent
        for j, suffix in enumerate(["gguf", "awq", f"chat-v{i}"]):
            child = f"user{j}/{word}-7b-{suffix}"
            add(child, 100 + i * 3 + j, 500)
            edges.append({"child": child, "parent": f"org{i}/{word}-7b", "parent_unlisted": False})
        add(f"anon{i}/{word}-7b-exl2", 400 + i, 300)  # orphan copy: should get org{i}/{word}-7b
    return rows, edges


class FakeClient:
    """Answers the two read queries and records the writes."""

    def __init__(self, rows, edges):
        self.rows, self.edges, self.writes = rows, edges, []

    def read(self, cypher, params=None):  # pylint: disable=unused-argument
        return self.rows if cypher == lp.MODELS_QUERY else self.edges

    def write(self, cypher, params=None):
        self.writes.append((cypher, params))
        return []


def test_run_learns_better_than_chance_and_writes_hypotheses():
    client = FakeClient(*synthetic_graph())
    info = lp.run(client, lp.Settings(candidates=20, max_train=500, max_test=200))

    by_name = {e.name: e for e in info["test"]}
    chosen = info["chosen"]["model (best validation MRR)"]
    assert set(by_name) == {"name baseline", "logistic_regression", "gradient_boosting", f"{chosen} + name rule"}
    assert info["data"]["candidate recall (test)"] == 1.0
    assert by_name[chosen].hits_at_1 >= 0.8

    assert client.writes[0][0] == lp.CLEAR_QUERY  # old predictions removed first
    written = [row for cypher, params in client.writes if cypher == lp.WRITE_QUERY for row in params["rows"]]
    assert written, "orphan copies should get a predicted parent"
    top = {row["child"]: row["parent"] for row in written if row["rank"] == 1}
    good = sum(top.get(f"anon{i}/model{i}-7b-exl2") == f"org{i}/model{i}-7b" for i in range(60))
    assert good >= 45
    assert all(not row["child"].startswith("org") for row in written)  # base models are not orphans
    assert "| model without declared parent |" in lp.render_report(info)


def test_run_stops_cleanly_on_a_graph_without_lineage():
    rows, _ = synthetic_graph(3)
    client = FakeClient(rows, [])
    info = lp.run(client, lp.Settings())
    assert info["written"] == {"predictions": 0}
    assert not client.writes
    assert "Link prediction report" in lp.render_report(info)


def test_name_rule_blocks_unrelated_candidates():
    class Always:  # a model that is sure of everything
        def predict_proba(self, features):
            return np.column_stack([np.zeros(len(features)), np.ones(len(features))])

    features = np.zeros((2, len(FEATURES)))
    features[0, FEATURES.index("name_score")] = 0.9
    features[1, FEATURES.index("name_score")] = 0.1
    assert list(lp.plausible_scores(Always(), features)) == [1.0, 0.0]


def test_orphans_need_a_date_downloads_and_few_children():
    ids = ["a/x", "b/y", "c/z", "d/w", "e/v"]
    table = _table(ids, downloads=[500, 500, 50, 500, 500], created=[1.0, 2.0, 3.0, None, 5.0])
    assert lp.orphans(table, has_parent={1}, child_counts=[0, 0, 0, 0, 12]) == [0]
