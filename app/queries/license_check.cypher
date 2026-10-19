// The license check, for one or many models at once ($ids is a list, so the
// recommendation endpoint checks its whole shortlist in ONE round trip
// instead of one query per candidate).
// For each model: its own license, every ancestor within __DEPTH__
// generations with the license it declares and the shortest path to it, and
// the training datasets of the model and of its ancestors with their license.
// The verdict is computed in Python from these facts so the rules stay testable.
UNWIND $ids AS model_id
MATCH (m:Model {id: model_id})
OPTIONAL MATCH (m)-[:HAS_LICENSE]->(own:License)
CALL (m) {
  MATCH p = (m)-[:__LINEAGE__*1..__DEPTH__]->(a:Model)
  WITH a, p ORDER BY length(p)
  WITH a, collect(p)[0] AS shortest
  OPTIONAL MATCH (a)-[:HAS_LICENSE]->(l:License)
  RETURN collect({
    id: a.id,
    unlisted: a:Unlisted,
    distance: length(shortest),
    path: [n IN nodes(shortest) | n.id],
    rel_types: [r IN relationships(shortest) | type(r)],
    license: l.id,
    family: l.family,
    commercial_use: l.commercial_use
  }) AS ancestors
}
CALL (m) {
  // *0.. so the model's own datasets are included (distance 0)
  MATCH p = (m)-[:__LINEAGE__*0..__DEPTH__]->(x:Model)-[:TRAINED_ON]->(d:Dataset)
  WITH d, x, length(p) - 1 AS distance ORDER BY distance
  WITH d, collect(x.id)[0] AS used_by, min(distance) AS distance
  OPTIONAL MATCH (d)-[:HAS_LICENSE]->(dl:License)
  RETURN collect({
    id: d.id,
    used_by: used_by,
    distance: distance,
    listed: coalesce(d.listed, false),
    license: dl.id,
    family: dl.family,
    commercial_use: dl.commercial_use
  }) AS datasets
}
RETURN m.id AS id,
       m.lineage_depth AS lineage_depth,
       own {.id, .family, .commercial_use, .url, .notes} AS license,
       ancestors,
       datasets
