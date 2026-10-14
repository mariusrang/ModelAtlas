// Models derived from $id, up to __DEPTH__ generations below, most downloaded
// first. Keyset pagination: instead of SKIP (which reads and drops every
// skipped row), the client sends the (downloads, id) of the last row it got.
MATCH (m:Model {id: $id})
MATCH p = (d:Model)-[:__LINEAGE__*1..__DEPTH__]->(m)
// rel_type = how d is attached to its own parent on the path.
WITH d, min(length(p)) AS distance, collect(type(relationships(p)[0]))[0] AS rel_type,
     coalesce(d.downloads, 0) AS dl
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
