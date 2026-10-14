// Everything shown on a model page. Each OPTIONAL part is a COLLECT subquery,
// so a model with 0 datasets still returns one row (no Cartesian product
// between datasets x papers x languages).
MATCH (m:Model {id: $id})
OPTIONAL MATCH (m)-[:PUBLISHED_BY]->(o:Organization)
OPTIONAL MATCH (m)-[:HAS_LICENSE]->(l:License)
RETURN m {.*, unlisted: m:Unlisted, created_at: toString(m.created_at), last_modified: toString(m.last_modified)} AS model,
       o.id AS organization,
       l {.id, .family, .commercial_use, .url} AS license,
       COLLECT { MATCH (m)-[:TRAINED_ON]->(d:Dataset) RETURN d.id ORDER BY d.id } AS datasets,
       COLLECT { MATCH (m)-[:CITES]->(p:Paper) RETURN p.arxiv_id ORDER BY p.arxiv_id } AS papers,
       COLLECT { MATCH (m)-[:SUPPORTS]->(g:Language) RETURN g.code ORDER BY g.code } AS languages,
       COLLECT { MATCH (m)-[:HAS_ARCHITECTURE]->(a:Architecture) RETURN a.id } AS architectures,
       COLLECT {
         MATCH (m)-[r:__LINEAGE__]->(p:Model)
         RETURN {id: p.id, rel_type: type(r), declared: r.declared, unlisted: p:Unlisted}
       } AS parents
