# Query benchmarks

Measured at 2026-10-16T12:39:36+00:00 on Neo4j 5.26.32, with 156,611 Model nodes.
Data: model_cards dump at commit `ce0b4cd50b6253d526765077de9e8d95e743f086` (local download manifest).
Node counts depend on this snapshot and can differ from the repository's reference load report.
The reference report on main has 156,071 models; the newer snapshot measured here has 156,611.
PR #5 pins the older model_cards commit `99b6ce9d9d47f796700745ed9630556c91c00439` in `config/sources.toml`.
Each variant has 3 warm-up runs and 30 timed runs. p50 and p95 use linear interpolation. Latency is client wall time including all returned records. Execution order alternates each pair. PROFILE runs separately; db hits are summed over its whole tree.
All paired answers are checked, including row order, warm-ups and the PROFILE run. The literal test checks every id pair. A naive traversal timeout is the only allowed exception. The 20 s limit is a server transaction timeout, not a hard client deadline.

These are warm-cache laptop measurements, not universal speed guarantees. The cache is not cleared, and compilation is not isolated. No index is created or removed. A speed-up below 1.00x means the optimized variant was slower.

| Experiment | Naive p50 ms | Optimized p50 ms | Speed-up | Naive db hits | Optimized db hits |
| --- | ---: | ---: | ---: | ---: | ---: |
| Author lookup | 95.878 | 18.688 | 5.13x | 313696 | 1423 |
| LIMIT early | 131.758 | 69.857 | 1.89x | 157042 | 157042 |
| Literal vs parameter | 2.944 | 2.242 | 1.31x | 2 | 2 |
| Cartesian product | 309.960 | 14.883 | 20.83x | 1977633 | 7316 |
| Deep pagination | 9.785 | 4.424 | 2.21x | 10043 | 45 |
| Bounded traversal | 64.698 | 64.711 | 1.00x | 7677 | 18441 |
| Precomputed descendants | 6.832 | 3.290 | 2.08x | 16610 | 3 |

## Complete measurements

| Experiment | Variant | p50 ms | p95 ms | Rows | Db hits | Status |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| Author lookup | naive | 95.878 | 153.137 | 473 | 313696 | ok |
| Author lookup | optimized | 18.688 | 25.133 | 473 | 1423 | ok |
| LIMIT early | naive | 131.758 | 176.896 | 10 | 157042 | ok |
| LIMIT early | optimized | 69.857 | 104.592 | 10 | 157042 | ok |
| Literal vs parameter | naive | 2.944 | 4.394 | 1 | 2 | ok |
| Literal vs parameter | optimized | 2.242 | 4.648 | 1 | 2 | ok |
| Cartesian product | naive | 309.960 | 343.025 | 322 | 1977633 | ok |
| Cartesian product | optimized | 14.883 | 18.299 | 322 | 7316 | ok |
| Deep pagination | naive | 9.785 | 13.566 | 20 | 10043 | ok |
| Deep pagination | optimized | 4.424 | 9.795 | 20 | 45 | ok |
| Bounded traversal | naive | 64.698 | 81.964 | 1891 | 7677 | ok |
| Bounded traversal | optimized | 64.711 | 97.747 | 1891 | 18441 | ok |
| Precomputed descendants | naive | 6.832 | 9.453 | 1 | 16610 | ok |
| Precomputed descendants | optimized | 3.290 | 6.188 | 1 | 3 | ok |

## Author lookup

The property filter scans Model nodes. The connected pattern starts from the unique Organization id.

Measured median speed-up: 5.13x. The optimized median latency was lower, and its db-hit count was lower. Small latency differences alone should not be treated as a reliable improvement.

Naive:

Observed operators (leaves first): NodeByLabelScan, Filter, Projection, Sort, ProduceResults.

```cypher
MATCH (m:Model) WHERE m.author = $author RETURN m.id AS id ORDER BY id
```

Optimized:

Observed operators (leaves first): NodeUniqueIndexSeek, Expand(All), Filter, Projection, Sort, ProduceResults.

```cypher
MATCH (:Organization {id:$author})<-[:PUBLISHED_BY]-(m:Model) RETURN m.id AS id ORDER BY id
```

Parameters:

```json
{
  "author": "Qwen",
  "org": "Qwen",
  "license": "apache-2.0",
  "task": "text-generation",
  "limit": 10,
  "offset": 5000,
  "page_size": 20,
  "last_downloads": 17342,
  "last_id": "OpenMed/OpenMed-ZeroShot-NER-Species-Multi-209M",
  "root": "Qwen/Qwen2.5-7B",
  "depth": 6
}
```

## LIMIT early

Collecting the full ordered result keeps all matching models in memory. LIMIT allows a Top operator or an ordered index scan to keep only the requested rows. Both variants still read the same candidates and properties, so db hits can match. Db hits do not count sorting, allocating the collection or copying maps; Top keeps a small candidate set instead.

Measured median speed-up: 1.89x. The optimized median latency was lower, and its db-hit count was unchanged. Small latency differences alone should not be treated as a reliable improvement.

Naive:

Observed operators (leaves first): NodeIndexSeek, CacheProperties, Projection, Sort, EagerAggregation, Unwind, Projection, Sort, ProduceResults.

```cypher
MATCH (m:Model {task:$task}) WITH m ORDER BY m.downloads DESC, m.id WITH collect({id:m.id, downloads:m.downloads}) AS models UNWIND models[..$limit] AS model RETURN model.id AS id, model.downloads AS downloads ORDER BY downloads DESC, id
```

Optimized:

Observed operators (leaves first): NodeIndexSeek, CacheProperties, Projection, Top, ProduceResults.

```cypher
MATCH (m:Model {task:$task}) RETURN m.id AS id, m.downloads AS downloads ORDER BY downloads DESC, id LIMIT $limit
```

Parameters:

```json
{
  "author": "Qwen",
  "org": "Qwen",
  "license": "apache-2.0",
  "task": "text-generation",
  "limit": 10,
  "offset": 5000,
  "page_size": 20,
  "last_downloads": 17342,
  "last_id": "OpenMed/OpenMed-ZeroShot-NER-Species-Multi-209M",
  "root": "Qwen/Qwen2.5-7B",
  "depth": 6
}
```

## Literal vs parameter

Each timed pair looks up the same real id. The literal changes between calls across 30 ids. Neo4j 5 auto-parameterizes literals, so this is not proof of a fresh plan compilation on every call. Explicit parameters keep the query text stable and avoid manual string escaping.

Measured median speed-up: 1.31x. The optimized median latency was lower, and its db-hit count was unchanged. Small latency differences alone should not be treated as a reliable improvement.

Naive:

Observed operators (leaves first): NodeUniqueIndexSeek, Projection, ProduceResults.

```cypher
MATCH (m:Model {id:__LITERAL__}) RETURN m.id AS id
```

Optimized:

Observed operators (leaves first): NodeUniqueIndexSeek, Projection, ProduceResults.

```cypher
MATCH (m:Model {id:$id}) RETURN m.id AS id
```

Parameters:

```json
{
  "author": "Qwen",
  "org": "Qwen",
  "license": "apache-2.0",
  "task": "text-generation",
  "limit": 10,
  "offset": 5000,
  "page_size": 20,
  "last_downloads": 17342,
  "last_id": "OpenMed/OpenMed-ZeroShot-NER-Species-Multi-209M",
  "root": "Qwen/Qwen2.5-7B",
  "depth": 6
}
```

## Cartesian product

The disconnected form can combine candidates before checking relationships. The connected pattern starts from one publisher and checks its models' licenses. The actual planner may also rewrite the naive form, so syntax alone does not prove that a CartesianProduct operator was used.

Measured median speed-up: 20.83x. The optimized median latency was lower, and its db-hit count was lower. Small latency differences alone should not be treated as a reliable improvement.

Naive:

Observed operators (leaves first): NodeUniqueIndexSeek, NodeByLabelScan, Argument, Expand(All), Filter, SemiApply, CartesianProduct, Argument, Expand(Into), SemiApply, Projection, Sort, ProduceResults.

```cypher
MATCH (m:Model), (o:Organization {id:$org}) WHERE (m)-[:PUBLISHED_BY]->(o) AND (m)-[:HAS_LICENSE]->(:License {id:$license}) RETURN m.id AS id ORDER BY id
```

Optimized:

Observed operators (leaves first): NodeUniqueIndexSeek, Expand(All), Filter, Expand(All), Filter, Distinct, Sort, ProduceResults.

```cypher
MATCH (:Organization {id:$org})<-[:PUBLISHED_BY]-(m:Model)-[:HAS_LICENSE]->(:License {id:$license}) RETURN DISTINCT m.id AS id ORDER BY id
```

Parameters:

```json
{
  "author": "Qwen",
  "org": "Qwen",
  "license": "apache-2.0",
  "task": "text-generation",
  "limit": 10,
  "offset": 5000,
  "page_size": 20,
  "last_downloads": 17342,
  "last_id": "OpenMed/OpenMed-ZeroShot-NER-Species-Multi-209M",
  "root": "Qwen/Qwen2.5-7B",
  "depth": 6
}
```

## Deep pagination

SKIP discards 5,000 sorted rows. Keyset pagination filters by the previous page's final (downloads, id) pair, including ties. Cursor discovery is outside the timed section because a normal client already has that cursor. An explicit downloads upper bound enables a range seek; PartialTop can order only the equal-download groups by id.

Measured median speed-up: 2.21x. The optimized median latency was lower, and its db-hit count was lower. Small latency differences alone should not be treated as a reliable improvement.

Naive:

Observed operators (leaves first): NodeIndexScan, Projection, PartialTop, Skip, ProduceResults.

```cypher
MATCH (m:Model) WHERE m.downloads IS NOT NULL RETURN m.id AS id, m.downloads AS downloads ORDER BY downloads DESC, id SKIP $offset LIMIT $page_size
```

Optimized:

Observed operators (leaves first): NodeIndexSeekByRange, Filter, Projection, PartialTop, ProduceResults.

```cypher
MATCH (m:Model) WHERE m.downloads <= $last_downloads AND (m.downloads < $last_downloads OR m.id > $last_id) RETURN m.id AS id, m.downloads AS downloads ORDER BY downloads DESC, id LIMIT $page_size
```

Parameters:

```json
{
  "author": "Qwen",
  "org": "Qwen",
  "license": "apache-2.0",
  "task": "text-generation",
  "limit": 10,
  "offset": 5000,
  "page_size": 20,
  "last_downloads": 17342,
  "last_id": "OpenMed/OpenMed-ZeroShot-NER-Species-Multi-209M",
  "root": "Qwen/Qwen2.5-7B",
  "depth": 6
}
```

## Bounded traversal

Both variants return distinct descendants within six hops. The naive pattern expands without a bound and filters path length afterwards; the optimized pattern puts the bound in the expansion. An unrestricted result would include deeper descendants and would be a different answer. The naive variant has a 20 s server transaction timeout. Breadth-first pruning checks and deduplicates nodes; on a small family, this can increase db hits despite the depth cap.

Measured median speed-up: 1.00x. The optimized median latency was not lower, and its db-hit count was higher. Small latency differences alone should not be treated as a reliable improvement.

Naive:

Observed operators (leaves first): NodeUniqueIndexSeek, VarLengthExpand(All), Filter, Distinct, Sort, ProduceResults.

```cypher
MATCH path = (c:Model)-[:FINETUNED_FROM|ADAPTER_OF|QUANTIZED_FROM|MERGED_FROM*]->(m:Model {id:$root}) WHERE length(path) <= $depth RETURN DISTINCT c.id AS id ORDER BY id
```

Optimized:

Observed operators (leaves first): NodeUniqueIndexSeek, VarLengthExpand(Pruning,BFS,All), Filter, Projection, Sort, ProduceResults.

```cypher
MATCH (c:Model)-[:FINETUNED_FROM|ADAPTER_OF|QUANTIZED_FROM|MERGED_FROM*1..6]->(m:Model {id:$root}) RETURN DISTINCT c.id AS id ORDER BY id
```

Parameters:

```json
{
  "author": "Qwen",
  "org": "Qwen",
  "license": "apache-2.0",
  "task": "text-generation",
  "limit": 10,
  "offset": 5000,
  "page_size": 20,
  "last_downloads": 17342,
  "last_id": "OpenMed/OpenMed-ZeroShot-NER-Species-Multi-209M",
  "root": "Qwen/Qwen2.5-7B",
  "depth": 6
}
```

## Precomputed descendants

The live count visits the descendant graph and removes duplicates. The stored count needs one id lookup and a property read. This shifts work to ETL; the property must be refreshed after graph changes.

Measured median speed-up: 2.08x. The optimized median latency was lower, and its db-hit count was lower. Small latency differences alone should not be treated as a reliable improvement.

Naive:

Observed operators (leaves first): NodeUniqueIndexSeek, VarLengthExpand(Pruning,BFS,All), Filter, EagerAggregation, ProduceResults.

```cypher
MATCH (c:Model)-[:FINETUNED_FROM|ADAPTER_OF|QUANTIZED_FROM|MERGED_FROM*]->(m:Model {id:$root}) RETURN count(DISTINCT c) AS descendants
```

Optimized:

Observed operators (leaves first): NodeUniqueIndexSeek, Projection, ProduceResults.

```cypher
MATCH (m:Model {id:$root}) RETURN m.descendant_count AS descendants
```

Parameters:

```json
{
  "author": "Qwen",
  "org": "Qwen",
  "license": "apache-2.0",
  "task": "text-generation",
  "limit": 10,
  "offset": 5000,
  "page_size": 20,
  "last_downloads": 17342,
  "last_id": "OpenMed/OpenMed-ZeroShot-NER-Species-Multi-209M",
  "root": "Qwen/Qwen2.5-7B",
  "depth": 6
}
```

For the literal pair, replace `__LITERAL__` with a JSON-quoted id from the first 30 Model ids in ascending order; supply the same id as `$id` to the parameterized query. The list cycles if more than 30 calls are requested.

Neo4j's [parameter documentation](https://neo4j.com/docs/cypher-manual/5/syntax/parameters/) describes auto-parameterization. The [driver summary documentation](https://neo4j.com/docs/python-manual/current/result-summary/) explains PROFILE metrics.
