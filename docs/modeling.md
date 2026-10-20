# Modeling choices

This page explains how we turned the Hugging Face dumps into a graph, and why.
Every number comes from [`reports/data_profile.md`](../reports/data_profile.md) or
[`reports/load_report.md`](../reports/load_report.md) (full graph, October 2026 dump).

## 1. Start from the questions

We did not start from "what is in the dump" but from the questions a pre-sales engineer
gets, because they decide what has to be a node (something we traverse or group by)
and what can stay a property (something we only read or filter on).

| Question | What the graph must do |
| --- | --- |
| Where does this model come from? | walk lineage edges up to a root |
| Can my client use it commercially? | read the license of the model, of every ancestor, and of the training data |
| Which small, French, commercially usable model should we start from? | filter by task / size / language, then check the lineage licenses |
| How are these two models related? | common ancestor + shortest path |
| Who builds on Mistral's models? | walk lineage edges down, group by publisher |

## 2. Nodes or properties?

| Concept | Choice | Why |
| --- | --- | --- |
| License | **node** (78 of them) | 115k models share 78 licenses, and the rules (family, commercial use) live on the license. As a property we would copy the rules 115k times and could not update them in one place (`POST /admin/license-rules/reload` updates 78 nodes). Datasets point to the same License nodes, so one rule covers models and data. |
| Organization | **node** | "who builds on X" groups models by publisher; as a string property it would be a full scan + group by. |
| Dataset | **node** (15.5k) | shared by many models, and it has its own facts from the second source (license, languages, size). |
| Paper | **node** (9.4k) | shared, and "models citing the same paper" is a one-hop query. |
| Language | **node** | "French models" and "French datasets" use the same node; 206k `SUPPORTS` edges for 184 languages. |
| Architecture | **node** (70) | grouping and agent questions ("which models are Llama-based?"). |
| Task | **both** a node and `Model.task` | the property is what the recommendation filters on (range index `model_task`, most selective filter first); the node is for browsing and for the agent's schema. We accept the duplication because the ETL writes both from the same column. |
| downloads, likes, params_b, created_at | properties | numbers we sort and filter on, all indexed. |
| formats (gguf, mlx...) | list property | only displayed, never traversed. |
| Hub tags | **not stored as a list** | a flat list mixes 6 concepts (license, parents, datasets, papers, languages, noise) and can't be indexed; `scripts/etl/tags.py` splits it into the nodes above. |
| Card text | not in the graph (yet) | ~1.5 GB of text; we only keep its length and whether it is the empty template. |

## 3. Lineage relationships

- **Four types** (`FINETUNED_FROM`, `ADAPTER_OF`, `QUANTIZED_FROM`, `MERGED_FROM`) instead of one
  `DERIVED_FROM {kind}`. Neo4j filters on the relationship type while it expands a path, but a
  property filter is only checked after the relationship is read. "Only fine-tunes" is then a cheaper
  pattern, and the types read like the Hub's own `base_model_relation` vocabulary.
- **Direction child → parent** ("A is fine-tuned from B"). A model has 1 parent most of the time
  (6,574 merges have several) but a popular base has thousands of children, so walking *up* is cheap
  and walking *down* is what we bound and paginate.
- The Hub types almost every edge (60 untyped out of 398k). Untyped ones are stored as
  `FINETUNED_FROM` with `declared = false`, so we never lose the fact that we guessed the kind.
- Counts in the full graph: 47,560 quantized, 36,907 fine-tune, 10,614 adapter, 6,700 merge.

## 4. Models that are not in the dump: the `Unlisted` label

22% of lineage edges in the raw dump point to a parent that is not public anymore (deleted, private
or gated). We keep those parents as `:Model:Unlisted` nodes rather than a separate label:

- they share the `Model.id` uniqueness constraint, so if the model shows up in a later dump, the same
  `MERGE` simply removes the extra label;
- every lineage query still works without special cases, and the license check can say
  "part of the lineage is not public" (`incomplete_lineage`).

## 5. Precomputed properties

`lineage_depth`, `child_count`, `descendant_count` and `root_ids` are computed in Python at load time
(`transform.lineage_metrics`). They are shown on every model page, and computing them per request
would mean walking the whole family tree each time (a popular base can have thousands of descendants).
The cost is that they are only as fresh as the last `make seed`, which is fine for a monthly dump.

## 6. Results of graph algorithms

`make gds` writes `pagerank`, `family_id`, `community` and `embedding` on Model nodes, and
`SIMILAR_TO {score}` relationships between models. We keep them as properties (with range indexes on
the first three) and not as `Family` / `Community` nodes: they are recomputed after every load, they are
only filtered or sorted on, and a new node type would add nothing to traverse. `SIMILAR_TO` is the
exception because "similar models" is something we do traverse.

## 7. Where each fact comes from

- every relationship has a `source` property: `card_metadata` (model cards) or `dataset_card`;
- `Dataset.listed = false` marks datasets cited by models but with no public card (their license is unknown);
- the API adds an `evidence` label to each answer: `VERIFIED` (read from a card), `DERIVED` (our rule
  applied to verified facts, like the license verdict). Predictions from the ML and agent steps will be
  stored on separate relationship types with a score, never mixed with declared edges.

## 8. Alternatives we rejected

| Alternative | Why we dropped it |
| --- | --- |
| One `DERIVED_FROM` relationship with a `kind` property | type filtering during expansion is cheaper and clearer (section 3) |
| License as a string property on Model | rules duplicated 115k times, no shared node with datasets |
| Keep all 861k models | 66% have 0 downloads and ~105k are auto-generated spam (`blockassist` swarms); scope rule in `config/scope.toml` keeps 150k real models + their ancestors |
| Separate `User` and `Organization` labels | the dump does not say whether an author is a person or a company, so we would be guessing |
| Store the tag list as-is | not indexable, mixes unrelated concepts (section 2) |
| Recompute lineage metrics in Cypher on each request | full traversal per page view (section 5) |

## 9. Known limits

- Licenses are **self-declared** by uploaders and can be wrong; the license check is informational.
- Parameter counts are guessed from the model name (`params_estimated = true`).
- Only 60.7% of the cited dataset ids (9,400 of 15,495) match a dataset card: some datasets were
  renamed (e.g. `squad` became `rajpurkar/squad`), others are private or deleted. The other 39.3% stay
  `Dataset {listed: false}` with an unknown license (`reports/etl_report.md`).
- Many cards declare no parent at all (e.g. `mistralai/Mistral-7B-Instruct-v0.2` does not cite
  `Mistral-7B-v0.2`): that is the gap the link-prediction step will try to fill, as hypotheses.
