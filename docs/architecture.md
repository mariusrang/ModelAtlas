# Architecture

## Services, ports and data flows

Only Nginx is published on the network. Neo4j and Ollama are bound to `127.0.0.1`
(so we can use Neo4j Browser and run the ETL from a laptop), the API and the UI
are only reachable inside the compose network.

```mermaid
flowchart LR
    user(["Browser / curl"])
    subgraph host["docker compose (network: modelatlas_default)"]
        nginx["nginx :80<br/>reverse proxy<br/>rate limit on /api/agent/"]
        ui["ui :8501<br/>Streamlit"]
        api["api :8000<br/>FastAPI"]
        neo4j[("neo4j :7687 bolt / :7474 browser<br/>Neo4j 5.26 + APOC + GDS")]
        ollama["ollama :11434<br/>local LLM (qwen2.5 7B)"]
    end
    etl["ETL on the laptop<br/>make data / make seed"]
    hf[("Hugging Face Hub<br/>model cards + dataset cards")]

    user -- "http :80" --> nginx
    nginx -- "/ (websocket)" --> ui
    nginx -- "/api/* (prefix stripped)" --> api
    ui -- "REST (API_BASE_URL)" --> api
    api -- "bolt, READ sessions, 10 s timeout" --> neo4j
    api -- "HTTP (agent step)" --> ollama
    hf -- "Parquet, pinned commit" --> etl
    etl -- "bolt 127.0.0.1:7687, batched UNWIND" --> neo4j
```

## Inside the API

```mermaid
flowchart TB
    r["routers/<br/>HTTP only: params, status codes"] --> s["services/<br/>rules, evidence labels, pagination<br/>(pure functions, unit-tested)"]
    s --> repo["repositories/graph_repository.py<br/>one method = one query"]
    repo --> q["queries/*.cypher<br/>parameters only, bounded depth"]
    q --> db[(Neo4j)]
```

- **routers** never contain Cypher, **services** never build Cypher strings, the **repository**
  only fills two tokens (`__LINEAGE__`, `__DEPTH__`) that can't be Cypher parameters, and the depth
  is checked to be an int between 1 and 12 before.
- Errors become one JSON shape `{code, detail, request_id}`; every response carries
  `X-Request-ID` and `X-Response-Time-ms`.

## ETL pipeline

```mermaid
flowchart LR
    a["extract.py<br/>download + manifest<br/>(commit, sha256)"] --> b["profile.py<br/>reports/data_profile.md"]
    b --> c["transform.py<br/>scope, spam, self-loops,<br/>ancestors, cycles, metrics"]
    d["dataset_cards.py<br/>2nd source, cited datasets only"] --> c
    c --> e["data/staged/*.parquet<br/>+ etl_report.md"]
    e --> f["load.py<br/>constraints first,<br/>UNWIND batches of 5,000"]
    f --> g["validate.py<br/>counts, cycles, constraints<br/>-> load_report.md"]
```

Every step can be re-run on its own, and the load is idempotent (`MERGE` everywhere), so running
`make seed` twice gives the same graph.

## CI

| Workflow | What it checks |
| --- | --- |
| `lint.yml` | black, isort, pylint >= 9.5 with the course `.pylintrc`, `make help`, gitleaks secret scan |
| `test.yml` | unit + API tests with coverage >= 60%, then the real compose stack: health of every container, `make seed-sample`, integration tests on the loaded graph |
| `docker.yml` | builds both images on every PR, pushes them to GHCR on `main` |
