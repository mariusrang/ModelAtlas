// "Who builds on this organization's models?" Start from the organization
// (unique-constraint seek), walk DOWN lineage up to __DEPTH__ generations,
// then group the derived models by their own publisher. One connected
// pattern, no Cartesian product.
MATCH (o:Organization {id: $id})
CALL (o) {
  MATCH (o)<-[:PUBLISHED_BY]-(m:Model)
  RETURN count(m) AS own_models, sum(m.downloads) AS own_downloads
}
CALL (o) {
  MATCH (o)<-[:PUBLISHED_BY]-(:Model)<-[:__LINEAGE__*1..__DEPTH__]-(d:Model)
  WITH DISTINCT d
  MATCH (d)-[:PUBLISHED_BY]->(builder:Organization)
  WHERE builder <> o
  WITH builder, count(d) AS derived
  ORDER BY derived DESC
  RETURN sum(derived) AS derived_models, collect({organization: builder.id, models: derived})[..$limit] AS builders
}
CALL (o) {
  MATCH (o)<-[:PUBLISHED_BY]-(:Model)<-[r:__LINEAGE__]-(:Model)
  WITH type(r) AS rel_type, count(r) AS n
  // ending on an aggregation without grouping key = always exactly one row
  RETURN collect({rel_type: rel_type, count: n}) AS direct_children_by_kind
}
RETURN o.id AS id, own_models, own_downloads, derived_models, builders, direct_children_by_kind
