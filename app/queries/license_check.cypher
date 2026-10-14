// The signature query. For one model: its own license, then every ancestor
// within __DEPTH__ generations with the license it declares, the shortest
// path to it and the kind of each hop. The verdict (is commercial use OK?)
// is computed in Python from these facts, so the rules stay testable.
MATCH (m:Model {id: $id})
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
RETURN m.id AS id,
       own {.id, .family, .commercial_use, .url, .notes} AS license,
       ancestors
