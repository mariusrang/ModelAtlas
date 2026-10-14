// Same as connection.cypher, used when the two models share no ancestor
// (the CALL in connection.cypher then returns no row and drops everything).
MATCH (a:Model {id: $a})
MATCH (b:Model {id: $b})
OPTIONAL MATCH sp = shortestPath((a)-[:__LINEAGE__*..__DEPTH__]-(b))
RETURN a.id AS a, b.id AS b, null AS common,
       CASE WHEN sp IS NULL THEN null ELSE {
         nodes: [n IN nodes(sp) | n.id],
         rel_types: [r IN relationships(sp) | type(r)]
       } END AS shortest_path,
       COLLECT { MATCH (a)-[:TRAINED_ON]->(d:Dataset)<-[:TRAINED_ON]-(b) RETURN d.id } AS shared_datasets,
       COLLECT { MATCH (a)-[:CITES]->(p:Paper)<-[:CITES]-(b) RETURN p.arxiv_id } AS shared_papers,
       EXISTS { (a)-[:PUBLISHED_BY]->(:Organization)<-[:PUBLISHED_BY]-(b) } AS same_organization
