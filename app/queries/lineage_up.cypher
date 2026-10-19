// Paths from the model up to its root models, bounded by __DEPTH__.
// A path stops either at a root (a model with no parent: complete = true) or
// at the depth limit (complete = false). Keeping the cut paths matters: if we
// dropped them, a model deeper than the limit would look like it has no root.
// Typed relationships in the pattern = only lineage edges are expanded.
// Shortest paths first, capped by $max_paths because merges multiply paths.
MATCH (m:Model {id: $id})
OPTIONAL MATCH p = (m)-[:__LINEAGE__*1..__DEPTH__]->(top:Model)
WHERE length(p) = __DEPTH__ OR NOT EXISTS { (top)-[:__LINEAGE__]->() }
WITH m, p,
     CASE WHEN top IS NULL THEN false ELSE NOT EXISTS { (top)-[:__LINEAGE__]->() } END AS complete
ORDER BY length(p)
WITH m, collect({path: p, complete: complete})[..$max_paths] AS found
RETURN m.id AS id,
       [f IN found WHERE f.path IS NOT NULL | {
          nodes: [n IN nodes(f.path) | n {.id, .downloads, unlisted: n:Unlisted}],
          rel_types: [r IN relationships(f.path) | type(r)],
          complete: f.complete
       }] AS paths
