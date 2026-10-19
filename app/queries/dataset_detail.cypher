// A training dataset: what its card declares (second data source) and the
// most downloaded models trained on it. COUNT {} and COLLECT {} keep it to one
// row whatever the number of models.
MATCH (d:Dataset {id: $id})
OPTIONAL MATCH (d)-[:HAS_LICENSE]->(l:License)
RETURN d {.*, created_at: toString(d.created_at)} AS dataset,
       l {.id, .family, .commercial_use, .url, .notes} AS license,
       COLLECT { MATCH (d)-[:SUPPORTS]->(g:Language) RETURN g.code ORDER BY g.code } AS languages,
       COUNT { (d)<-[:TRAINED_ON]-(:Model) } AS model_count,
       COLLECT {
         MATCH (d)<-[:TRAINED_ON]-(m:Model)
         WITH m ORDER BY m.downloads DESC LIMIT $limit
         OPTIONAL MATCH (m)-[:HAS_LICENSE]->(ml:License)
         RETURN m {.id, .author, .downloads, .likes, .task, .params_b, .params_estimated,
                   unlisted: m:Unlisted, license: ml.id}
       } AS top_models
