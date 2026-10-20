// Parents guessed by `make predict` for a model that declares none.
// PREDICTED_PARENT edges are HYPOTHESIS: kept apart from the declared lineage.
MATCH (m:Model {id: $id})
CALL (m) {
  OPTIONAL MATCH (m)-[r:PREDICTED_PARENT]->(p:Model)
  WITH r, p
  WHERE p IS NOT NULL
  ORDER BY r.rank
  LIMIT $limit
  OPTIONAL MATCH (p)-[:HAS_LICENSE]->(l:License)
  RETURN collect({
    model: p {.id, .author, .downloads, .likes, .task, .params_b, .params_estimated,
              unlisted: p:Unlisted, license: l.id},
    score: r.score, rank: r.rank, model_version: r.model_version,
    name_score: r.name_score, content_cosine: r.content_cosine
  }) AS predictions
}
RETURN m.id AS id,
       COUNT { (m)-[:__LINEAGE__]->() } AS declared_parents,
       EXISTS { ()-[:PREDICTED_PARENT]->() } AS predictions_ready,
       predictions
