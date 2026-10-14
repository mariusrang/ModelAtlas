# Cypher queries

One query per file, loaded by name by `app/repositories/query_store.py`.

- Values are **always** `$parameters` (plan cache reuse, no injection).
- Two tokens are replaced before sending, because Cypher does not allow
  parameters there: `__LINEAGE__` (the fixed list of lineage relationship
  types) and `__DEPTH__` (an int already validated by the API, max 12).
- Every variable-length pattern is bounded by `__DEPTH__`.
