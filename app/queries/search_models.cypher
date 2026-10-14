// Search bar + the agent's entity resolver. The fulltext index (model_search)
// matches words in the id and author; we then boost popular models, otherwise
// "mistral 7b" would rank 500 obscure re-uploads above mistralai's own model.
CALL db.index.fulltext.queryNodes('model_search', $query) YIELD node, score
WHERE $task IS NULL OR node.task = $task
WITH node, score * log10(coalesce(node.downloads, 0) + 10) AS rank
ORDER BY rank DESC
LIMIT $limit
OPTIONAL MATCH (node)-[:HAS_LICENSE]->(l:License)
RETURN node {.id, .author, .downloads, .likes, .task, .params_b, .params_estimated,
             unlisted: node:Unlisted, license: l.id} AS model,
       round(rank, 3) AS rank
