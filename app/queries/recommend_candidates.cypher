// Candidate models for a client brief. The task filter goes first because it
// is the most selective and uses the model_task index; the other filters are
// cheap property checks on that subset. License must be declared and allow
// commercial use (true or "conditional") when $commercial is true; the API
// then re-checks every ancestor with license_check before answering.
MATCH (m:Model {task: $task})
WHERE NOT m:Unlisted
  AND ($max_params IS NULL OR (m.params_b IS NOT NULL AND m.params_b <= $max_params))
  AND ($min_downloads IS NULL OR m.downloads >= $min_downloads)
  AND ($language IS NULL OR EXISTS { (m)-[:SUPPORTS]->(:Language {code: $language}) })
OPTIONAL MATCH (m)-[:HAS_LICENSE]->(l:License)
WITH m, l
WHERE NOT $commercial OR l.commercial_use IN [true, 'conditional']
WITH m, l
ORDER BY m.downloads DESC
LIMIT $pool
RETURN m {.id, .author, .downloads, .likes, .task, .params_b, .params_estimated, .descendant_count,
          unlisted: false, license: l.id} AS model,
       l.family AS license_family,
       l.commercial_use AS commercial_use,
       COLLECT { MATCH (m)-[:SUPPORTS]->(g:Language) RETURN g.code } AS languages
