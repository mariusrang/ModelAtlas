// Models similar to $id: SIMILAR_TO edges written by Node Similarity (Jaccard
// on the datasets and papers both models declare). The algorithm writes one
// edge per direction, so we match both ways and keep the best score.
MATCH (m:Model {id: $id})
CALL (m) {
  OPTIONAL MATCH (m)-[s:SIMILAR_TO]-(o:Model)
  WITH o, max(s.score) AS score
  WHERE o IS NOT NULL
  ORDER BY score DESC, o.downloads DESC
  LIMIT $limit
  OPTIONAL MATCH (o)-[:HAS_LICENSE]->(l:License)
  RETURN collect({
    model: o {.id, .author, .downloads, .likes, .task, .params_b, .params_estimated,
              unlisted: o:Unlisted, license: l.id},
    score: score,
    shared_datasets: COUNT { (m)-[:TRAINED_ON]->(:Dataset)<-[:TRAINED_ON]-(o) },
    shared_papers: COUNT { (m)-[:CITES]->(:Paper)<-[:CITES]-(o) }
  }) AS similar
}
RETURN m.id AS id, m.pagerank IS NOT NULL AS gds_ready, similar
