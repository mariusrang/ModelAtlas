// Models derived from $id, up to __DEPTH__ generations below, most downloaded
// first. Keyset pagination: instead of SKIP (which reads and drops every
// skipped row), the client sends the (downloads, id) of the last row it got.
// Keyset avoids SKIP, not the expansion: every page still walks all the paths
// below $id before sorting, so page 50 costs about the same as page 1 (the
// dominant cost for a big family like Qwen, ~24k derived models).
MATCH (m:Model {id: $id})
MATCH p = (d:Model)-[:__LINEAGE__*1..__DEPTH__]->(m)
// distance = shortest path from d to m; rel_type = how d is attached to its parent on
// that shortest path (sorted by length then type, so the same row always gets the same
// rel_type when d has several parents).
WITH d, length(p) AS hops, type(relationships(p)[0]) AS first_rel
ORDER BY hops ASC, first_rel ASC
WITH d, min(hops) AS distance, collect(first_rel)[0] AS rel_type, coalesce(d.downloads, 0) AS dl
WHERE ($relation IS NULL OR rel_type = $relation)
  AND ($after_downloads IS NULL
       OR dl < $after_downloads
       OR (dl = $after_downloads AND d.id > $after_id))
WITH d, distance, rel_type, dl
ORDER BY dl DESC, d.id ASC
LIMIT $limit
OPTIONAL MATCH (d)-[:HAS_LICENSE]->(l:License)
RETURN d {.id, .author, .downloads, .likes, .task, .params_b, .params_estimated,
          unlisted: d:Unlisted, license: l.id} AS model,
       distance, rel_type
