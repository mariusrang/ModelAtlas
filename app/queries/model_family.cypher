// Where a model sits in its family: its WCC family (everything connected by
// lineage), its Louvain community (a tighter sub-family) and its PageRank rank
// inside the family. All counts use the family_id / community range indexes.
MATCH (m:Model {id: $id})
RETURN m.id AS id,
       m.pagerank AS pagerank,
       m.family_id AS family_id,
       m.community AS community,
       CASE WHEN m.family_id IS NULL THEN 0
            ELSE COUNT { MATCH (x:Model {family_id: m.family_id}) } END AS family_size,
       CASE WHEN m.family_id IS NULL THEN null
            ELSE COUNT { MATCH (x:Model {family_id: m.family_id}) WHERE x.pagerank > m.pagerank } + 1
       END AS rank_in_family,
       CASE WHEN m.community IS NULL THEN 0
            ELSE COUNT { MATCH (x:Model {community: m.community}) } END AS community_size,
       CASE WHEN m.community IS NULL THEN []
            ELSE COLLECT {
              MATCH (x:Model {community: m.community})
              WITH x ORDER BY x.pagerank DESC LIMIT $limit
              RETURN x {.id, .downloads, .pagerank}
            } END AS top_members
