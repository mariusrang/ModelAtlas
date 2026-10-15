<!--
Raw answer, kept unchanged (anti-genericity appendix, see README.md in this folder).
Assistant: Claude (Sonnet), a second new session with no project context and no files.
Date: 2026-10-15. Prompt: the exact text quoted in README.md, section 1.
-->

# Neo4j schema: Hugging Face open model ecosystem

## Design decisions

1. **Lineage is one relationship type** (`BASED_ON`) with a `kind` property, not four types. This makes "trace to the root" a single variable-length query, `-[:BASED_ON*]->`. HF's own `base_model:<kind>:<id>` tags map to `kind` directly.
2. **License is a node with normalized commercial-use flags.** Model cards use free-form licenses such as `llama3`, `gemma` and `openrail`. Normalizing them into fields you can filter on is what makes the commercial check answerable.
3. **Benchmark scores are properties on a relationship**, not a node. There is one edge per model, benchmark and metric.
4. **Task, Language and Architecture are nodes**, so recommendations can be graph traversals instead of string filters.

## Node labels and key properties

| Label | Key properties |
|---|---|
| `Model` | `id` (repo id, e.g. `meta-llama/Llama-3.1-8B`), `name`, `params_b`, `context_length`, `pipeline_tag`, `library`, `format` (safetensors, gguf, onnx), `quant` (e.g. Q4_K_M, AWQ), `dtype`, `gated`, `downloads_30d`, `likes`, `created_at`, `last_modified`, `card_url`, `embedding` (vector of the model card), plus derived `root_model_ids`, `commercial_status` and `commercial_checked_at` |
| `Organization` | `handle`, `name`, `kind` (org or user), `verified`, `country`, `url` |
| `License` | `spdx_id` or `key`, `name`, `url`, `commercial_use` (`allowed`, `conditional`, `prohibited`, `unknown`), `attribution_required`, `copyleft`, `derivatives_allowed`, `mau_threshold`, `has_use_restrictions` (AUP or RAIL), `requires_same_license_on_derivatives` |
| `Dataset` | `id`, `name`, `size_rows`, `modality`, `synthetic` (bool), `downloads` |
| `Paper` | `arxiv_id`, `title`, `year`, `abstract`, `url` |
| `Benchmark` | `name` (e.g. MMLU, IFEval), `category`, `higher_is_better`, `url` |
| `Task` | `name` (text-generation, embedding, ASR, ...), the HF pipeline tag |
| `Language` | `code`, `name` |
| `Architecture` | `name` (LlamaForCausalLM, Mistral, ...), `family` |

Optional additions:
- `ModelFamily` (Llama 3, Qwen2.5) for grouping.
- `Tag` for free-form tags.
- `Hardware` for VRAM profiles. You can start by computing `est_vram_gb` as a property on `Model`.

## Relationships

```
(:Model)-[:BASED_ON {kind, method, weight, source}]->(:Model)
(:Model)-[:LICENSED_UNDER]->(:License)
(:Dataset)-[:LICENSED_UNDER]->(:License)
(:Organization)-[:PUBLISHED]->(:Model)
(:Organization)-[:PUBLISHED]->(:Dataset)
(:Model)-[:TRAINED_ON {stage}]->(:Dataset)
(:Model)-[:DESCRIBED_IN]->(:Paper)
(:Dataset)-[:DESCRIBED_IN]->(:Paper)
(:Paper)-[:INTRODUCES]->(:Benchmark)
(:Paper)-[:INTRODUCES]->(:Model)
(:Model)-[:EVALUATED_ON {metric, score, split, n_shots, source, verified, date}]->(:Benchmark)
(:Benchmark)-[:MEASURES]->(:Task)
(:Model)-[:SOLVES]->(:Task)
(:Model)-[:SUPPORTS]->(:Language)
(:Model)-[:HAS_ARCHITECTURE]->(:Architecture)
(:Model)-[:IN_FAMILY]->(:ModelFamily)
```

Notes on the properties:
- **`BASED_ON.kind`** is one of `finetune`, `adapter`, `quantized`, `merge`. Add `base` for a root model whose parent is unknown or absent.
- **`BASED_ON.weight` and `method`** are for merges. A merge has several outgoing `BASED_ON` edges, each with its mixing weight and a `method` such as slerp, ties or dare.
- **`BASED_ON.source`** records provenance: `card_metadata`, `config_json` or `inferred`. Inferred edges let you flag low-confidence lineage.
- **`TRAINED_ON.stage`** is `pretrain`, `sft` or `preference`.
- **`EVALUATED_ON.source`** says where a score came from: `model_card`, `open_llm_leaderboard` or `self_reported`. Together with `verified`, it lets you down-weight unverified scores.

## Constraints and indexes

```cypher
CREATE CONSTRAINT model_id    IF NOT EXISTS FOR (n:Model)        REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT org_handle  IF NOT EXISTS FOR (n:Organization) REQUIRE n.handle IS UNIQUE;
CREATE CONSTRAINT lic_key     IF NOT EXISTS FOR (n:License)      REQUIRE n.key IS UNIQUE;
CREATE CONSTRAINT dataset_id  IF NOT EXISTS FOR (n:Dataset)      REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT paper_arxiv IF NOT EXISTS FOR (n:Paper)        REQUIRE n.arxiv_id IS UNIQUE;
CREATE CONSTRAINT bench_name  IF NOT EXISTS FOR (n:Benchmark)    REQUIRE n.name IS UNIQUE;
CREATE CONSTRAINT task_name   IF NOT EXISTS FOR (n:Task)         REQUIRE n.name IS UNIQUE;
CREATE CONSTRAINT lang_code   IF NOT EXISTS FOR (n:Language)     REQUIRE n.code IS UNIQUE;
CREATE CONSTRAINT arch_name   IF NOT EXISTS FOR (n:Architecture) REQUIRE n.name IS UNIQUE;

CREATE CONSTRAINT model_required IF NOT EXISTS FOR (n:Model) REQUIRE n.id IS NOT NULL;      -- Enterprise
CREATE CONSTRAINT lic_commercial IF NOT EXISTS FOR (n:License) REQUIRE n.commercial_use IS NOT NULL; -- Enterprise

CREATE INDEX model_task_dl IF NOT EXISTS FOR (n:Model) ON (n.pipeline_tag, n.downloads_30d);
CREATE INDEX model_params  IF NOT EXISTS FOR (n:Model) ON (n.params_b);
CREATE FULLTEXT INDEX model_text IF NOT EXISTS FOR (n:Model) ON EACH [n.id, n.name];
CREATE VECTOR INDEX model_embedding IF NOT EXISTS FOR (n:Model) ON (n.embedding)
  OPTIONS {indexConfig: {`vector.dimensions`: 768, `vector.similarity_function`: 'cosine'}};
```

Constraints that aren't enforceable in Neo4j (validate during ingest):
- `BASED_ON` must not form a cycle.
- `kind` must be in the allowed set.
- A model has at most one `LICENSED_UNDER` edge.

## How the three user goals map to queries

**1. Where does this model come from?**
```cypher
MATCH p = (m:Model {id:$id})-[:BASED_ON*1..10]->(root:Model)
WHERE NOT (root)-[:BASED_ON]->()
RETURN p;
// Datasets and papers across the whole lineage:
MATCH (m:Model {id:$id})-[:BASED_ON*0..10]->(a:Model)-[:TRAINED_ON|DESCRIBED_IN]->(x)
RETURN DISTINCT a.id, labels(x)[0], x;
```

**2. Can I use it commercially?** The model's own license is not enough, because a fine-tune of a restricted base inherits the restriction. Check every ancestor and every training dataset, taking the most restrictive result.
```cypher
MATCH (m:Model {id:$id})-[:BASED_ON*0..10]->(a:Model)-[:LICENSED_UNDER]->(l:License)
OPTIONAL MATCH (a)-[:TRAINED_ON]->(:Dataset)-[:LICENSED_UNDER]->(dl:License)
RETURN a.id, l.key, l.commercial_use, l.mau_threshold, l.attribution_required,
       collect(DISTINCT dl.key) AS dataset_licenses;
```
Cache the result in `Model.commercial_status` with `commercial_checked_at`. Recompute it when any ancestor or license changes. Treat `unknown` or a missing license as "not safe", not as "allowed". Say in the UI that this is guidance, not legal advice.

**3. Recommend a model.**
```cypher
MATCH (m:Model)-[:SOLVES]->(:Task {name:$task}),
      (m)-[:SUPPORTS]->(:Language {code:$lang}),
      (m)-[e:EVALUATED_ON {metric:'acc'}]->(b:Benchmark {name:$bench})
WHERE m.commercial_status = 'allowed' AND m.params_b <= $maxParams AND e.verified
RETURN m.id, e.score, m.downloads_30d
ORDER BY e.score DESC LIMIT 10;
```
Similar-model suggestions combine the vector index with graph signals. Use the `embedding` vector, plus shared `Task`, `Architecture` and `TRAINED_ON` neighbours, plus shared lineage (siblings under the same parent).

## Ingestion notes

- **Source mapping.** HF's API and model-card tags give most of the data:
  - `base_model:*` and `base_model_relation` map to `BASED_ON`.
  - `license:` maps to `LICENSED_UNDER`.
  - `datasets:` maps to `TRAINED_ON`.
  - `arxiv:` maps to `DESCRIBED_IN`.
  - `model-index` results map to `EVALUATED_ON`.
  - `pipeline_tag` maps to `SOLVES`.
  - `language:` maps to `SUPPORTS`.
- **Missing parents.** Referenced base models may be deleted or private. Create stub `Model` nodes with `stub: true` so lineage edges aren't lost.
- **Model size.** Quantized repos often don't carry `params_b`. Inherit it from the parent via `BASED_ON`.
- **Hubs.** Popular bases such as Llama and Mistral will have 10k+ incoming `BASED_ON` edges. Always bound traversal depth and filter early.
- **Time.** Downloads and likes change daily. If you need trends, keep them as a daily time series (`MetricSnapshot` nodes or an external store), not as overwritten properties.
- **Licenses.** Seed `License` nodes by hand for the roughly 30 licenses that cover most models (apache-2.0, mit, llama2, llama3.x, gemma, openrail variants, cc-by-nc, etc.). Hand-curation is worth it because the commercial flags drive a user-facing answer.

I can also write this up as a shareable page, or generate a seed Cypher script with sample data, if you want either.
