<!--
Raw answer, kept unchanged (anti-genericity appendix, see README.md in this folder).
Assistant: Claude (Sonnet), new session with no project context and no files.
Date: 2026-10-15. Prompt: the exact text quoted in README.md, section 1.
-->

# Neo4j schema: Hugging Face model ecosystem

## Design decisions

- **Lineage is a DAG of `Model` nodes.** Merges have several parents, so one model can have many outgoing `DERIVED_FROM` edges.
- **Licenses are nodes with structured terms.** Commercial-use checks need to query them, not parse strings.
- **Benchmark scores are reified as `EvalResult` nodes.** A score has a metric, shot count, source and verification status, and it links a model to a benchmark.
- **Recommendations come from facts a user can filter on:** task, size, language, license, score, popularity. Embedding similarity sits on top of those.

## Node labels and key properties

| Label | Key properties |
|---|---|
| `Model` | `id` (repo id, e.g. `meta-llama/Llama-3.1-8B`), `name`, `sha`, `createdAt`, `lastModified`, `downloads`, `likes`, `trendingScore`, `gated`, `paramsB`, `contextLength`, `precision`, `format` (safetensors, gguf, awq, gptq...), `pipelineTag`, `modelType` (base, finetune, adapter, quantized, merge; derived), `isStub` (true for base models not found on HF), `effectiveCommercialUse` (materialized, see below), `cardEmbedding` |
| `Organization` | `name` (HF handle), `displayName`, `type` (company, lab, academic, community, individual), `verified`, `country` |
| `License` | `id` (SPDX or HF slug, e.g. `apache-2.0`, `llama3.1`), `name`, `family` (permissive, copyleft, openrail, custom, noncommercial), `commercialUse` (allowed, conditional, prohibited), `attributionRequired`, `shareAlike`, `derivativesAllowed`, `mauThreshold`, `hasAcceptableUsePolicy`, `usageRestrictions` (list), `url` |
| `Dataset` | `id`, `name`, `kind` (pretrain, sft, preference, eval), `sizeRows`, `synthetic`, `gated`, `downloads` |
| `Paper` | `id` (arXiv id or DOI), `title`, `year`, `abstract` |
| `Benchmark` | `id`, `name`, `category` (reasoning, code, math, chat, multilingual...), `higherIsBetter` |
| `EvalResult` | `id`, `metric` (e.g. `acc_norm`), `value`, `shots`, `split`, `source` (self-reported, leaderboard, model-index), `verified`, `evaluatedAt` |
| `Task` | `id` (e.g. `text-generation`), `name`, `modality` |
| `Language` | `code` (ISO 639) |
| `Architecture` | `name` (e.g. `LlamaForCausalLM`), `family` |
| `Library` | `name` (transformers, peft, llama.cpp, vllm...) |
| `Tag` (optional) | `name`, for free-form tags such as `roleplay` or `function-calling` |
| `Author` (optional) | `name`, for paper authors |

## Relationship types

```
(:Model)-[:DERIVED_FROM {relation, source}]->(:Model)
(:Model)-[:LICENSED_UNDER]->(:License)
(:Dataset)-[:LICENSED_UNDER]->(:License)
(:Model)-[:TRAINED_ON {stage, source}]->(:Dataset)
(:Dataset)-[:GENERATED_BY]->(:Model)            // synthetic data provenance
(:Dataset)-[:DERIVED_FROM]->(:Dataset)
(:Model)-[:PUBLISHED_BY]->(:Organization)
(:Dataset)-[:PUBLISHED_BY]->(:Organization)
(:Model|Dataset|Benchmark)-[:DESCRIBED_IN]->(:Paper)   // Benchmark: introduced in
(:Paper)-[:AUTHORED_BY]->(:Author)
(:Author)-[:AFFILIATED_WITH]->(:Organization)
(:Model)-[:HAS_RESULT]->(:EvalResult)-[:ON_BENCHMARK]->(:Benchmark)
(:Model)-[:FOR_TASK]->(:Task)
(:Model)-[:SUPPORTS_LANGUAGE]->(:Language)
(:Model)-[:HAS_ARCHITECTURE]->(:Architecture)
(:Model)-[:USES_LIBRARY]->(:Library)
(:Model)-[:HAS_TAG]->(:Tag)
(:Benchmark)-[:FOR_TASK]->(:Task)
(:Model)-[:SIMILAR_TO {score}]->(:Model)        // precomputed with GDS or embeddings
```

- **`DERIVED_FROM.relation`** takes one of `finetune`, `adapter`, `quantized`, `merge`. This mirrors the `base_model_relation` field in HF model cards. A single type keeps traversal simple (`DERIVED_FROM*1..`), and you filter on the property when you need one kind. A merge is a model with two or more outgoing edges.
- **`source`** records where an edge came from (`card_metadata`, `config.json`, `inferred_from_name`). Lineage extracted from model cards is often missing or wrong, so this lets users judge confidence.

## Constraints and indexes (Neo4j 5)

```cypher
CREATE CONSTRAINT model_id   IF NOT EXISTS FOR (n:Model)        REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT org_name   IF NOT EXISTS FOR (n:Organization) REQUIRE n.name IS UNIQUE;
CREATE CONSTRAINT lic_id     IF NOT EXISTS FOR (n:License)      REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT ds_id      IF NOT EXISTS FOR (n:Dataset)      REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT paper_id   IF NOT EXISTS FOR (n:Paper)        REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT bench_id   IF NOT EXISTS FOR (n:Benchmark)    REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT eval_id    IF NOT EXISTS FOR (n:EvalResult)   REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT task_id    IF NOT EXISTS FOR (n:Task)         REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT lang_code  IF NOT EXISTS FOR (n:Language)     REQUIRE n.code IS UNIQUE;
CREATE CONSTRAINT arch_name  IF NOT EXISTS FOR (n:Architecture) REQUIRE n.name IS UNIQUE;
CREATE CONSTRAINT lib_name   IF NOT EXISTS FOR (n:Library)      REQUIRE n.name IS UNIQUE;

CREATE INDEX model_downloads IF NOT EXISTS FOR (m:Model) ON (m.downloads);
CREATE INDEX model_params    IF NOT EXISTS FOR (m:Model) ON (m.paramsB);
CREATE INDEX model_type      IF NOT EXISTS FOR (m:Model) ON (m.modelType);
CREATE FULLTEXT INDEX model_text IF NOT EXISTS FOR (m:Model) ON EACH [m.id, m.description];
CREATE VECTOR INDEX model_embedding IF NOT EXISTS FOR (m:Model) ON (m.cardEmbedding)
  OPTIONS {indexConfig: {`vector.dimensions`: 768, `vector.similarity_function`: 'cosine'}};
```

Existence and type constraints (`IS NOT NULL`, `IS :: STRING`) and node keys need Enterprise Edition. On Community, enforce them in the loader. `DERIVED_FROM` must stay acyclic, and this cannot be expressed as a constraint. Check it in the ingestion pipeline, for example by rejecting an edge when the parent already has a path to the child.

## How the three user needs map to the graph

**1. Trace where a model comes from**

```cypher
MATCH p = (m:Model {id:$id})-[:DERIVED_FROM*1..]->(root:Model)
WHERE NOT (root)-[:DERIVED_FROM]->()
RETURN p
```

Add `-[:TRAINED_ON]->(:Dataset)` and `-[:DESCRIBED_IN]->(:Paper)` on each ancestor to get full provenance.

**2. Can I use it commercially?**

The effective answer is the most restrictive of the model's own license, its ancestors' licenses and, optionally, the licenses of its training data. For example, a model with an Apache-2.0 card that is fine-tuned from a non-commercial base is not commercially usable.

```cypher
MATCH (m:Model {id:$id})
OPTIONAL MATCH (m)-[:DERIVED_FROM*0..]->(a:Model)-[:LICENSED_UNDER]->(l:License)
WITH m, collect(DISTINCT {model:a.id, license:l.id, commercial:l.commercialUse, mau:l.mauThreshold}) AS chain
RETURN m.id, chain,
       CASE WHEN any(x IN chain WHERE x.commercial='prohibited') THEN 'no'
            WHEN any(x IN chain WHERE x.commercial='conditional') THEN 'conditional'
            ELSE 'yes' END AS commercialUse
```

Materialize the result in `Model.effectiveCommercialUse` during ingestion so recommendation queries can filter on it cheaply. Return the chain as the explanation. A bare "no" is not enough; users need to see which ancestor blocks it.

**3. Recommendations**

Filter on facts, then rank:

```cypher
MATCH (m:Model)-[:FOR_TASK]->(:Task {id:'text-generation'}),
      (m)-[:SUPPORTS_LANGUAGE]->(:Language {code:'de'}),
      (m)-[:HAS_RESULT]->(r:EvalResult)-[:ON_BENCHMARK]->(:Benchmark {id:'mmlu'})
WHERE m.effectiveCommercialUse = 'yes' AND m.paramsB <= 9
RETURN m.id, r.value, m.downloads
ORDER BY r.value DESC LIMIT 10
```

For "more like this model", use `SIMILAR_TO` or a vector search on `cardEmbedding`. For "best fine-tune of X", traverse `DERIVED_FROM` in reverse and rank by benchmark score.

## Notes and trade-offs

- **Stub models:** base models referenced in cards but missing from HF (private, removed or closed-source) become `Model {isStub:true}`. This keeps lineage chains from breaking.
- **Quantized and adapter variants:** these are separate `Model` nodes. In recommendation queries, collapse them with `WHERE m.modelType IN ['base','finetune','merge']` unless the user wants a specific format (GGUF, AWQ).
- **Benchmark score reliability:** scores are mostly self-reported, so keep `source` and `verified` on `EvalResult` and let queries filter on them.
- **Licenses with conditions:** some licenses (Llama, Gemma, OpenRAIL) allow commercial use only under conditions. Model these as `commercialUse:'conditional'` plus `mauThreshold` and `usageRestrictions`, instead of forcing a yes/no.
- **Versions over time:** if you need history, such as license changes between revisions, add `(:ModelRevision {sha})-[:REVISION_OF]->(:Model)`. For a first version, `Model.sha` and `lastModified` are enough.
