// Every path from the model up to a root (a model with no parent), bounded by
// __DEPTH__. Typed relationships in the pattern = only lineage edges are
// expanded, never PUBLISHED_BY etc. Shortest paths first, capped by $max_paths
// because merges can multiply the number of paths.
MATCH (m:Model {id: $id})
OPTIONAL MATCH p = (m)-[:__LINEAGE__*1..__DEPTH__]->(root:Model)
WHERE NOT (root)-[:__LINEAGE__]->()
WITH m, p ORDER BY length(p)
WITH m, collect(p)[..$max_paths] AS paths
RETURN m.id AS id,
       [p IN paths WHERE p IS NOT NULL | {
          nodes: [n IN nodes(p) | n {.id, .downloads, unlisted: n:Unlisted}],
          rel_types: [r IN relationships(p) | type(r)]
       }] AS paths
