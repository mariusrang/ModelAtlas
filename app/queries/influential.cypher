// Most influential models according to PageRank on the lineage graph
// (written by `make gds`). The range index on Model.pagerank lets Neo4j read
// the models already sorted instead of sorting 156k nodes.
MATCH (m:Model)
WHERE m.pagerank IS NOT NULL AND ($task IS NULL OR m.task = $task)
WITH m ORDER BY m.pagerank DESC LIMIT $limit
OPTIONAL MATCH (m)-[:HAS_LICENSE]->(l:License)
RETURN m {.id, .author, .downloads, .likes, .task, .params_b, .params_estimated,
          unlisted: m:Unlisted, license: l.id} AS model,
       m.pagerank AS pagerank, m.descendant_count AS descendant_count, m.family_id AS family_id
