# ModelAtlas

[![lint](https://github.com/mariusrang/ModelAtlas/actions/workflows/lint.yml/badge.svg)](https://github.com/mariusrang/ModelAtlas/actions/workflows/lint.yml)
[![tests](https://github.com/mariusrang/ModelAtlas/actions/workflows/test.yml/badge.svg)](https://github.com/mariusrang/ModelAtlas/actions/workflows/test.yml)
[![docker](https://github.com/mariusrang/ModelAtlas/actions/workflows/docker.yml/badge.svg)](https://github.com/mariusrang/ModelAtlas/actions/workflows/docker.yml)
![pylint](https://img.shields.io/badge/pylint-10.00-brightgreen)
![python](https://img.shields.io/badge/python-3.11%2B-blue)

**A Neo4j knowledge graph of the open AI model ecosystem on Hugging Face: who is derived from whom,
under which license, trained on which data.**

Most models on Hugging Face are built on other models: a fine-tune of a fine-tune, a quantized copy, a
merge of three models. Each one declares (or forgets to declare) a license, training datasets and papers.
We load two public Hugging Face dumps (model cards and dataset cards) into a graph to answer the questions
an AI pre-sales / solutions engineer gets from clients:

- *Where does this model come from?* (lineage up to the foundation model)
- *Can my client use it commercially?* (licenses of the model, of every ancestor and of the training data)
- *Which French-capable, < 8B, commercially usable model should we start from?*
- *How are these two models related?* (common ancestor, shortest path)
- *This GGUF lists no parent: what is it probably based on?* (link prediction, planned)

Each answer of the API says how much you can trust it: `VERIFIED` (read from a card), `DERIVED` (one of
our rules applied to verified facts) or, for the ML and agent steps, `HYPOTHESIS` with a confidence score.

> Course project for *Knowledge Graphs & Graph Data* (AIDAMS 3A), level 3+ (agentic / GraphRAG).

---

## Project status

| Part | Status |
| --- | --- |
| Repo skeleton, Docker stack, CI, `/health` | done |
| Data download + profiling | done |
| ETL (transform + load into Neo4j) | done |
| Graph queries + API endpoints | done |
| Second source: dataset cards (training-data licenses) | done |
| GDS + parent-model prediction | next |
| LangGraph agent with NL-to-Cypher + guardrails | planned |
| Streamlit UI, demo notebook | skeleton / planned |

---

## Quick start

Requirements: Docker Desktop, Python 3.11+, GNU `make` (on Windows: `winget install ezwinports.make`,
then reopen the terminal). No API key is needed: the LLM runs locally with Ollama.

```bash
git clone https://github.com/mariusrang/ModelAtlas.git
cd ModelAtlas
make install       # .venv + dependencies
make env           # creates .env from .env.example -> change NEO4J_PASSWORD and ADMIN_API_KEY
make docker-run    # builds and starts Neo4j, Ollama, API, UI and Nginx
make seed-sample   # small real sample (~1 min), or `make data` + `make seed` for the full graph
make ollama-pull   # first time only: downloads the local LLM (~4.7 GB)
```

Demo notebook: [demo.ipynb](demo.ipynb) (exploration + charts, needs the stack running).

Use a Python 3.12+ environment for the notebook (NetworkX 3.7 requires it), and
load the full graph with both model cards and dataset cards first. The dataset
analysis requires the dataset-card ETL from PR #5. Saved outputs record the data
revisions used for that execution.

```bash
python -m pip install -r requirements-notebook.txt
python -m jupyter lab demo.ipynb
```

Then open:

| What | URL |
| --- | --- |
| Web app | http://localhost |
| API docs (Swagger) | http://localhost/api/docs |
| Neo4j Browser | http://localhost:7474 |

---

## Data

| Source | What we use it for | Size |
| --- | --- | --- |
| [librarian-bots/model_cards_with_metadata](https://huggingface.co/datasets/librarian-bots/model_cards_with_metadata) | Every public model on the Hub: id, author, downloads, likes, task, tags (license, parents, datasets, papers, languages) | 861,627 rows, ~1.5 GB Parquet |
| [librarian-bots/dataset_cards_with_metadata](https://huggingface.co/datasets/librarian-bots/dataset_cards_with_metadata) | License, languages, size and task of the datasets our models cite (we read only those, metadata only) | ~631k rows, ~1 GB Parquet |

Both are public and need no API key. `make data` downloads them and writes a `manifest.json` per source
with the commit hash and the SHA-256 of every file; `make data-pin` then writes those commits into
[`config/sources.toml`](config/sources.toml), so anyone rebuilds the graph from the same data version.
`make profile` produces [`reports/data_profile.md`](reports/data_profile.md): the numbers behind our
modeling and scoping choices.

### From raw dump to graph

```bash
make data          # download (once)
make profile       # numbers behind every modeling decision -> reports/data_profile.md
make seed          # transform + load + validate -> reports/etl_report.md, reports/load_report.md
```

The transform applies rules from [`config/scope.toml`](config/scope.toml), each one backed by the profile:

| Rule | Why (from the profile) |
| --- | --- |
| keep models with >= 50 downloads or >= 1 like | 66% of the 861k models have 0 downloads; this keeps 146k real models |
| add back every ancestor of a kept model | otherwise family trees and license checks would have holes |
| drop `blockassist` / swarm checkpoints | 105k auto-generated models (12% of the dump) |
| drop self-loops, break cycles | 1,149 models declare themselves as their own parent |
| parents missing from the dump become `:Unlisted` nodes | 22% of lineage edges point to a model that is not public anymore |
| ignore arXiv `1910.09700` | cited by 126k models only because it is in the default card template |
| precompute depth, descendants, roots | shown on every model page; 99.6% of chains are <= 5 levels deep |
| read dataset cards only for cited datasets | 15.5k datasets are cited out of ~631k cards |

Result (full graph, [`reports/load_report.md`](reports/load_report.md)): 156,071 models (6,138 of them
`Unlisted`), 101,781 lineage edges, 43.5k organizations, 15.5k datasets, 9.4k papers, all validation checks
passed, loaded in about 100 s.

> On Windows, put `MODELATLAS_DATA_DIR=C:\modelatlas-data` in `.env` so the 1.5 GB download is not synced by OneDrive.

---

## Graph model

![Graph schema](docs/diagrams/graph_schema.png)

| Nodes | Key | Relationships (from `Model`) |
| --- | --- | --- |
| `Model` (+ `Unlisted` label for parents not in the dump) | `id` | `FINETUNED_FROM`, `ADAPTER_OF`, `QUANTIZED_FROM`, `MERGED_FROM` → `Model` |
| `Organization` | `id` | `PUBLISHED_BY` |
| `License` (with `family`, `commercial_use` rules) | `id` | `HAS_LICENSE` |
| `Dataset` (+ card facts: `listed`, size, task categories) | `id` | `TRAINED_ON`; a dataset also has `HAS_LICENSE` and `SUPPORTS` |
| `Paper` | `arxiv_id` | `CITES` |
| `Task` | `id` | `PERFORMS` |
| `Language` | `code` | `SUPPORTS` |
| `Architecture` | `id` | `HAS_ARCHITECTURE` |

8 uniqueness constraints, 7 range indexes and 1 fulltext index, all in [`app/database/schema.py`](app/database/schema.py)
with the query each one serves. Why each concept is a node or a property, why four lineage types and not one,
and the alternatives we rejected: [`docs/modeling.md`](docs/modeling.md). How our schema differs from the one an
AI assistant proposes from the same description: [`docs/anti_genericity/`](docs/anti_genericity/README.md).
License rules and their sources: [`docs/licenses.md`](docs/licenses.md).

---

## API

Interactive docs (Swagger): <http://localhost/api/docs> once the stack is up. Every response says how far
you can trust it with an `evidence` field: `VERIFIED` (declared in a model card), `DERIVED` (our rule
applied to verified facts) or `HYPOTHESIS` (ML/LLM guess, later steps).

| Endpoint | What it answers | Evidence |
| --- | --- | --- |
| `GET /health` | Is the API up, does Neo4j answer? | - |
| `GET /stats` | How big is the graph? | VERIFIED |
| `GET /models/search?q=mistral 7b` | Fulltext search on id and author, popular models first | VERIFIED |
| `GET /models/{id}` | Model page: publisher, license, datasets, papers, parents, lineage metrics | VERIFIED + DERIVED |
| `GET /models/{id}/lineage?depth=4` | Every path from the model up to its foundation model(s) | VERIFIED |
| `GET /models/{id}/descendants?relation=quantized&limit=20` | Models derived from it, keyset-paginated (`next_cursor`) | VERIFIED |
| `GET /models/{id}/license-check` | Can a company use it commercially, given **every ancestor's** license and the training data? | DERIVED |
| `GET /connection?a=...&b=...` | Closest common ancestor, shortest path, shared datasets/papers | DERIVED |
| `POST /recommend` | Shortlist for a client brief (task, size, language, commercial use) | DERIVED |
| `GET /organizations/{id}/footprint` | Who builds on an organization's models? | DERIVED |
| `GET /datasets/{id}` | Dataset card facts and the most downloaded models trained on it | VERIFIED |
| `POST /admin/license-rules/reload` | Re-apply `config/licenses.toml` without reloading the graph (needs `X-API-Key`) | - |

Model ids keep their slash (`/models/mistralai/Mistral-7B-v0.1/lineage`). Some examples (from cmd):

```bat
curl "http://localhost/api/models/search?q=mistral%207b&limit=3"
curl "http://localhost/api/models/mistralai/Mistral-7B-v0.1/descendants?depth=1&relation=quantized&limit=5"
curl "http://localhost/api/models/TheBloke/Mistral-7B-Instruct-v0.2-GGUF/license-check"
curl "http://localhost/api/connection?a=HuggingFaceH4/zephyr-7b-beta&b=mistralai/Mistral-7B-Instruct-v0.2"
curl -X POST "http://localhost/api/recommend" -H "Content-Type: application/json" ^
     -d "{\"task\": \"text-generation\", \"max_params_b\": 8, \"commercial_use\": true, \"limit\": 5}"
```

Design choices, briefly:

- **Layers**: routers (HTTP only) -> services (rules, evidence labels) -> repository -> one `.cypher` file per
  query in [`app/queries/`](app/queries/README.md). Cypher stays readable and can be `PROFILE`d in Neo4j Browser.
- **Safe by construction**: values are always `$parameters`; the search text is rebuilt word by word before it
  reaches Lucene; every variable-length pattern is bounded (`MAX_TRAVERSAL_DEPTH`, default 6, 400 above it);
  reads run in a READ session with a 10 s timeout.
- **License rule** (fail-safe): the verdict is the most restrictive status over the model **and all its ancestors**,
  since a fine-tune can't drop its parent's restrictions. Unknown or missing licenses give `needs_review`, never
  `ok`. A non-commercial training dataset also gives `needs_review` (legal grey zone, not a ban). Rules live in
  [`config/licenses.toml`](config/licenses.toml). Informational only, not legal advice.
- **No N+1**: the recommendation checks the licenses of its whole shortlist in one query (`UNWIND $ids`).
- **Pagination**: keyset on `(downloads, id)` instead of `SKIP`, so page 50 costs the same as page 1.

---

## Architecture

Five services run in `docker compose`; only Nginx is published on the network.

| Service | Image | Port | Role |
| --- | --- | --- | --- |
| `nginx` | `nginx:1.27-alpine` | 80 (public) | Reverse proxy: `/api` → API, `/` → UI, rate limit on the agent |
| `api` | `docker/api.Dockerfile` | 8000 (internal) | FastAPI, graph queries, agent, guardrails |
| `ui` | `docker/ui.Dockerfile` | 8501 (internal) | Streamlit web app |
| `neo4j` | `neo4j:5.26-community` + APOC + GDS | 7474 / 7687 (localhost only) | The graph |
| `ollama` | `ollama/ollama` | 11434 (localhost only) | Local LLM, no API key |

The UI never talks to Neo4j directly: every query goes through the API, which is where validation,
guardrails and evidence labels live. Diagrams of the services, the API layers and the ETL pipeline:
[`docs/architecture.md`](docs/architecture.md).

---

## Makefile

| Command | What it does |
| --- | --- |
| `make help` | List every command |
| `make install` | Create `.venv` and install dependencies |
| `make env` | Create `.env` from `.env.example` |
| `make run` | Run the API locally with auto-reload (port 8000) |
| `make ui` | Run the Streamlit app locally (port 8501) |
| `make docker-run` | Build and start the whole stack |
| `make docker-down` | Stop the stack (data is kept) |
| `make ollama-pull` | Download the local LLM into the Ollama container |
| `make data` | Download both raw dumps: model cards + dataset cards (~2.5 GB, once) |
| `make data-pin` | Pin `config/sources.toml` to the commits you downloaded |
| `make profile` | Profile the raw dump into `reports/data_profile.md` |
| `make fixture` | Rebuild the small test fixtures from the full dumps |
| `make seed` | Build the full graph in Neo4j (transform + load + validate) |
| `make seed-sample` | Load the small fixture into Neo4j (used by CI) |
| `make test` | All tests + coverage (integration tests skip if the stack is down) |
| `make test-unit` | Fast tests only, no Docker needed |
| `make test-integration` | Tests against the running stack |
| `make lint` | black + isort + pylint with the course `.pylintrc` (fails under 9.5) |
| `make format` | Auto-format the code |
| `make clean` | Remove caches and coverage files |
| `make tree` | Show the project tree |

---

## Project structure

```text
app/            FastAPI app (routers -> services -> repositories -> Neo4j)
ui/             Streamlit app
scripts/        ETL, training, benchmarks, Makefile helpers
tests/          unit/, api/, health/, integration/ (+ agent/ later)
docker/         Dockerfiles and Nginx config
config/         versioned config (data sources, license rules, scope)
docs/           modeling choices, architecture diagrams
reports/        generated numbers: data profile, ETL and load reports
.github/        CI workflows, PR template
```

---

## Team

| Member | GitHub | Main parts |
| --- | --- | --- |
| Marius Rang (lead) | [@mariusrang](https://github.com/mariusrang) | data pipeline, graph API, agent |
| Ylias Guetari | [@ylguet](https://github.com/ylguet) | license rules, schema diagram, anti-genericity appendix |
| Lucas Magret Delord | [@Lucasmd9](https://github.com/Lucasmd9) | query benchmarks, EXPLAIN/PROFILE, demo notebook |

Every PR is reviewed by another member before merge ([CONTRIBUTING.md](CONTRIBUTING.md)).

---

## How we used AI assistants

The course allows AI coding assistants, and we used one (Claude) to draft code, Cypher queries and
documentation. We ran, tested and reviewed everything on the real data ourselves, and we tied each
modeling choice to a number from our own data profile rather than to a generic schema
([`docs/modeling.md`](docs/modeling.md)). Each member can explain the parts listed next to their name.

---

## Disclaimer

ModelAtlas is a student project for educational purposes. License analysis is informational only and is
**not legal advice**. Model metadata on Hugging Face is self-declared by uploaders and can be wrong; we show
where every fact comes from so you can check it.
