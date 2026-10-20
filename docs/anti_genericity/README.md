# Anti-genericity appendix

The course asks us to prove that our graph model is not the first schema any AI assistant would give. So we
asked AI assistants to design the schema from the same project description, and we compare their answers with
what we built. Every number below comes from our reports: [`data_profile.md`](../../reports/data_profile.md)
(raw dump, 861,627 models), [`etl_report.md`](../../reports/etl_report.md) and
[`load_report.md`](../../reports/load_report.md) (the graph we load).

## 1. How we did it

- **Date:** 15 October 2026.
- **Assistants:** two new Claude (Sonnet) sessions with no project context: no files, no repository, no earlier
  messages. Two earlier attempts were thrown away because the assistant opened our repository and copied our
  schema, which defeats the purpose.
- **Prompt** (identical in both sessions, pasted as is):

```text
  Design a Neo4j graph schema for this project: "A knowledge graph of the open AI model ecosystem on
  Hugging Face: models, their base models (fine-tunes, adapters, quantized versions, merges), licenses,
  training datasets, papers, benchmarks and organizations. Users want to trace where a model comes from,
  check if it can be used commercially, and get model recommendations."
  Give node labels, relationship types, key properties and constraints.
```

- The raw answers are kept **unchanged** in [`generic_schema_1.md`](generic_schema_1.md) and
  [`generic_schema_2.md`](generic_schema_2.md). We call them G1 and G2.

Honest first remark: both answers are good. Both make `License` a node with rules, both allow several parents for
a merge, both have a stub for missing parents. The differences are in the details that only appear once you look
at the real data.

## 2. Side by side

| Aspect | Generic schemas (G1, G2) | Our schema | Why ours (with our numbers) |
| --- | --- | --- | --- |
| Lineage | One type with a property: G1 `DERIVED_FROM {relation}`, G2 `BASED_ON {kind}` | 4 types: `FINETUNED_FROM`, `ADAPTER_OF`, `QUANTIZED_FROM`, `MERGED_FROM` (child → parent) | The kinds mean different things (a quantized copy *is* its parent's weights, a fine-tune is a new model) and have very different sizes in the graph: 47,560 / 36,907 / 10,614 / 6,700 edges. With types, the filter is in the pattern (`-[:QUANTIZED_FROM*]->`), so Neo4j never expands the other edges; with a property it expands all of them and filters afterwards. |
| Merges with several parents | Several edges allowed. G1 adds: "a merge is a model with two or more outgoing edges" | A merge is a model with `MERGED_FROM` edges | 6,574 models of the raw dump have several parents, and not all are merges: a GGUF can declare a `finetune` parent and a `quantized` parent (example in section 3). G1's rule counts it as a merge. |
| Parents missing from the dump | G1 `isStub` property, G2 `stub: true` property | Extra label `:Unlisted` on the `Model` node | 22% of declared lineage edges (88,691 of 398,422) point to a model that is not in the dump; we keep them as 6,138 `:Unlisted` nodes. A label is checked in the pattern (`WHERE NOT m:Unlisted` in the recommender) and the license check says `incomplete_lineage` when it meets one. |
| License | Node with rules. G2 keys it on `spdx_id` or `key` | Node keyed on the Hub id (`apache-2.0`, `llama3.1`); rules in a reviewed file, [`config/licenses.toml`](../../config/licenses.toml), applied at load | 80 `License` nodes shared by 122,463 `HAS_LICENSE` edges (115,244 from models, 7,219 from datasets). Hub ids such as `llama3.1` or `openrail++` have no SPDX id, so G2's key would be empty for them. A missing rule gives `needs_review`, never `ok`. |
| Tags | Task, Language, Architecture as nodes; G1 and G2 also suggest a free `Tag` node | Typed nodes only for a known list (70 `Architecture` nodes from [`scope.toml`](../../config/scope.toml)); weight formats stay a list property `formats` | Raw tags are mostly noise for our questions (`conversational` on 212,739 models, `gensyn` on 105,309). A `Tag` node would be a huge hub with no meaning. |
| Task | Node + property (G1 `FOR_TASK` + `pipelineTag`, G2 `SOLVES` + `pipeline_tag`) | Node (`PERFORMS`, 102,893 edges, 56 tasks) + indexed property `Model.task` | Same idea as the generic answers. The property is the first filter of the recommender (index `model_task`); the node is for browsing ("all tasks") and the agent. |
| Noise in the data | Not mentioned | Dropped by the ETL before loading | 105,310 spam models (`blockassist` / swarm checkpoints) dropped; 6,910 links to the template paper 1910.09700 dropped; 529 fake dataset links (`custom`, local files) dropped; 1,149 self-loops dropped and 1 cycle edge broken. |
| Precomputed metrics | G1 caches `effectiveCommercialUse`, G2 caches `commercial_status` and `root_model_ids` | `lineage_depth`, `child_count`, `descendant_count`, `root_ids` on every model | We cache structure, which only changes when the data is reloaded, and never the license verdict, which changes each time a rule is fixed. Chains go up to 33 levels deep, but 99.6% are ≤ 5, so depth bounds are cheap. |
| Predicted parents | Same edge with `source: inferred` (G1 `inferred_from_name`) | Planned: predictions stored apart from the real edges, as hypotheses with a confidence | A guessed edge in the same relationship type would be followed by every lineage and license query. Keeping it separate means a guess can never be shown as a fact. |
| Trust labels | Only on benchmark scores (G1 `verified`, G2 `verified`) | Every API answer says `VERIFIED`, `DERIVED` or `HYPOTHESIS`; lineage edges have `declared` | The 60 raw edges with no kind are loaded as `FINETUNED_FROM` with `declared = false`, so "we guessed the kind" is never lost. |

**Update: dataset licenses (second source).** Datasets now also come from a second source, the Hugging Face
dataset cards. A dataset points to the same `License` nodes as the models with `HAS_LICENSE`, so one rule in
`config/licenses.toml` covers both. 61.0% of the 15,495 datasets our models cite have a card, and 58 of them
declare several licenses, which we all keep. The license check now also looks at the training data of the model
and of its ancestors. Both generic schemas proposed licenses on datasets (section 4), so here we followed them; the
difference is that our version is backed by real card data, with its coverage measured in `reports/etl_report.md`.

## 3. Where the generic schema gives a WRONG answer

The classic mistake ("only one parent per merge" or "license as a string on the child") is not in these two
answers. The wrong answers come from the real data instead.

**Example 1: "Can I use this model commercially?"** Query from G1, shortened (G2's query has the same pattern):

```cypher
MATCH (m:Model {id: $id})
OPTIONAL MATCH (m)-[:DERIVED_FROM*0..]->(a:Model)-[:LICENSED_UNDER]->(l:License)
WITH m, collect(DISTINCT {model: a.id, commercial: l.commercialUse}) AS chain
RETURN CASE WHEN any(x IN chain WHERE x.commercial = 'prohibited') THEN 'no'
            WHEN any(x IN chain WHERE x.commercial = 'conditional') THEN 'conditional'
            ELSE 'yes' END AS commercialUse
```

Why it is wrong: the pattern only keeps ancestors that *have* a license. 46.64% of raw models declare none, and
missing parents (stubs) never do. A model whose only license is on itself says `apache-2.0` but whose base model is
unlicensed or missing gets **`yes`**. A model with no license anywhere also gets **`yes`** (every `any(...)` is
false). G2's version uses `MATCH` instead of `OPTIONAL MATCH`, so the same model returns **no row at all**.

Our query (the facts part of [`license_check.cypher`](../../app/queries/license_check.cypher), simplified): every
ancestor comes back, with or without a license, and Python turns "no license" or "unlisted" into `needs_review`.

```cypher
MATCH (m:Model {id: $id})-[:FINETUNED_FROM|ADAPTER_OF|QUANTIZED_FROM|MERGED_FROM*0..6]->(a:Model)
OPTIONAL MATCH (a)-[:HAS_LICENSE]->(l:License)
RETURN DISTINCT a.id AS model, a:Unlisted AS unlisted, l.id AS license, l.commercial_use AS commercial_use
```

**Example 2: "Which papers are the most cited by models?"** G2 maps every `arxiv:` tag to a `DESCRIBED_IN` edge,
and G1 has the same edge with no filter (G2 keys papers on `arxiv_id` instead of `id`):

```cypher
MATCH (:Model)-[:DESCRIBED_IN]->(p:Paper)
RETURN p.id AS paper, count(*) AS models ORDER BY models DESC LIMIT 3
```

Why it is wrong: neither answer filters anything, so on the dump the top answer is arXiv 1910.09700 with 126,239
models. It is "Quantifying the Carbon Emissions of Machine Learning", cited only because the link is in Hugging Face's default model-card template. The second
one, 2504.07091 (98,583 models), has almost the same count as the 98,591 `blockassist` spam models. Our ETL drops
template citations and spam before loading, so the same question on our graph gives real papers:

```cypher
MATCH (:Model)-[:CITES]->(p:Paper)
RETURN p.arxiv_id AS paper, count(*) AS models ORDER BY models DESC LIMIT 3
```

**Example 3: "How many merges are there?"** With G1's own definition ("a merge is a model with two or more
outgoing edges"):

```cypher
MATCH (m:Model)-[:DERIVED_FROM]->(p:Model)
WITH m, count(p) AS parents WHERE parents >= 2
RETURN count(m) AS merges
```

Why it is wrong: having two parents does not make a merge. `Dhptl/zephyr-7b-beta-GGUF` (in our test fixture)
declares `finetune: mistralai/Mistral-7B-v0.1` *and* `quantized: HuggingFaceH4/zephyr-7b-beta`. It is a quantized
copy, but G1 counts it as a merge, and a recommender that hides merges would hide it. On our graph the type says
what the Hub declared:

```cypher
MATCH (m:Model) WHERE EXISTS { (m)-[:MERGED_FROM]->(:Model) }
RETURN count(m) AS merges
```

## 4. What the generic schemas did better, or what we considered

- **Benchmarks.** Both have a `Benchmark` node (G1 with an `EvalResult` node, G2 with scores on an `EVALUATED_ON`
  edge). We don't have benchmarks yet; we plan to add a `Benchmark` node, and G2's "score on the edge, with
  `source` and `verified`" is the simpler option for us.
- **License of the training data.** Both put `LICENSED_UNDER` on `Dataset` too. They were right: it was a gap in
  our license check (a model trained on a non-commercial dataset may inherit the restriction), and we have now
  added it from the dataset cards (see the update at the end of section 2).
- **Similarity.** Both suggest `SIMILAR_TO` edges. We now compute them with GDS Node Similarity on the datasets and
  papers two models share, and store them as `SIMILAR_TO {score}` labelled DERIVED, never as declared facts.
- **Merge details.** G2 puts `method` and `weight` on merge edges. Good idea, but the Hub tags we load only say
  `base_model:merge:<id>`, so we would have to read every card text to fill them.
- **Things we chose not to do:** G1's `Author` and `Library` nodes and G2's `ModelFamily` node do not serve any of
  our use cases yet, and a cached commercial verdict on each model (both answers) would go stale every time we fix
  a license rule.

## 5. Conclusion

The generic answers are a good textbook schema: they get the big entities right. What they miss is everything
the data taught us: 22% of lineage edges point to missing models, almost half of the models have no license, 12% of
the dump is spam (105,310 models), and the most cited paper is a template. Our choices (typed lineage, `:Unlisted`, a fail-safe
license check, an ETL that cleans before loading, precomputed structure but no cached verdict) all come from
these numbers, and section 3 shows that skipping them gives wrong answers, not just slower ones.
