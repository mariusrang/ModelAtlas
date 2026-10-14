// How are two models related?
// 1. the closest common ancestor (each side walks UP at most __DEPTH__ hops;
//    *0.. includes the model itself, so "a is an ancestor of b" also works);
// 2. the shortest lineage path in either direction (shortestPath is a
//    bidirectional BFS, much cheaper than enumerating all paths);
// 3. what they share outside lineage (datasets, papers, organization).
MATCH (a:Model {id: $a})
MATCH (b:Model {id: $b})
CALL (a, b) {
  MATCH pa = (a)-[:__LINEAGE__*0..__DEPTH__]->(x:Model)
  WITH x, min(length(pa)) AS from_a
  MATCH pb = (b)-[:__LINEAGE__*0..__DEPTH__]->(x)
  WITH x, from_a, min(length(pb)) AS from_b
  RETURN x {.id, .downloads, distance_from_a: from_a, distance_from_b: from_b} AS common
  ORDER BY from_a + from_b ASC, x.downloads DESC
  LIMIT 1
}
OPTIONAL MATCH sp = shortestPath((a)-[:__LINEAGE__*..__DEPTH__]-(b))
RETURN a.id AS a, b.id AS b, common,
       CASE WHEN sp IS NULL THEN null ELSE {
         nodes: [n IN nodes(sp) | n.id],
         rel_types: [r IN relationships(sp) | type(r)]
       } END AS shortest_path,
       COLLECT { MATCH (a)-[:TRAINED_ON]->(d:Dataset)<-[:TRAINED_ON]-(b) RETURN d.id } AS shared_datasets,
       COLLECT { MATCH (a)-[:CITES]->(p:Paper)<-[:CITES]-(b) RETURN p.arxiv_id } AS shared_papers,
       EXISTS { (a)-[:PUBLISHED_BY]->(:Organization)<-[:PUBLISHED_BY]-(b) } AS same_organization
