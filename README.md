# ModelAtlas

[![lint](https://github.com/mariusrang/ModelAtlas/actions/workflows/lint.yml/badge.svg)](https://github.com/mariusrang/ModelAtlas/actions/workflows/lint.yml)
[![tests](https://github.com/mariusrang/ModelAtlas/actions/workflows/test.yml/badge.svg)](https://github.com/mariusrang/ModelAtlas/actions/workflows/test.yml)
[![docker](https://github.com/mariusrang/ModelAtlas/actions/workflows/docker.yml/badge.svg)](https://github.com/mariusrang/ModelAtlas/actions/workflows/docker.yml)
![pylint](https://img.shields.io/badge/pylint-10.00-brightgreen)
![python](https://img.shields.io/badge/python-3.11%2B-blue)

**A knowledge graph of the open AI model ecosystem, with an agent that always shows its sources.**

Hugging Face hosts hundreds of thousands of models, and most of them are built on top of other models:
a fine-tune of a fine-tune, a quantized copy, a merge of three models... Each one has a license, training
datasets, papers and benchmark scores. ModelAtlas puts all of that in a Neo4j graph so we can answer the
questions an AI pre-sales / solutions engineer gets every day:

- *Where does this model really come from?* (lineage up to the foundation model)
- *Can my client use it commercially?* (every license on every ancestor path)
- *Which French-capable, < 8B, commercially usable model should we start from?*
- *This GGUF has no parent listed: what is it probably based on?* (ML link prediction)

Every fact we return is labelled `VERIFIED` (declared in a model card), `DERIVED` (one of our rules applied
to verified data) or `HYPOTHESIS` (guessed by our ML model or the LLM, always with a confidence score).
We never present a guess as a fact.

> Course project for *Knowledge Graphs & Graph Data* (AIDAMS 3A), level 3+ (agentic / GraphRAG).

---

## Project status

| Part | Status |
| --- | --- |
| Repo skeleton, Docker stack, CI, `/health` | done |
| Data download + profiling | done |
| ETL (transform + load into Neo4j) | done |
| Graph queries + API endpoints | in progress |
| GDS + parent-model prediction | planned |
| LangGraph agent + guardrails | planned |
| Streamlit UI | skeleton |

---

## Quick start

Requirements: Docker Desktop, Python 3.11+, GNU `make` (on Windows: Git Bash, or `choco install make`).
No API key is needed: the LLM runs locally with Ollama.

```bash
git clone https://github.com/mariusrang/ModelAtlas.git
cd ModelAtlas
make env           # creates .env from .env.example -> edit the two passwords
make docker-run    # builds and starts Neo4j, Ollama, API, UI and Nginx
make ollama-pull   # first time only: downloads the local LLM (~4.7 GB)
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
| [librarian-bots/model_cards_with_metadata](https://huggingface.co/datasets/librarian-bots/model_cards_with_metadata) | Every public model on the Hub: id, author, downloads, likes, tags (license, parents, datasets, papers, languages), task, card text | ~817k rows, 1.5 GB Parquet |
| [librarian-bots/dataset_cards_with_metadata](https://huggingface.co/datasets/librarian-bots/dataset_cards_with_metadata) | Dataset nodes (license, languages) | Parquet |

Both are free and need no API key. `make data` downloads them and writes a `manifest.json` with the exact
commit hash and SHA-256 of every file, so the graph can always be rebuilt from the same data version.
`make profile` then produces [`reports/data_profile.md`](reports/data_profile.md): the numbers behind our
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
| keep models with >= 50 downloads or >= 1 like | 66% of the 861k models have 0 downloads; this keeps ~146k real models |
| add back every ancestor of a kept model | otherwise family trees and license checks would have holes |
| drop `blockassist` / swarm checkpoints | ~98.6k auto-generated models (11% of the dump) |
| drop self-loops, break cycles | 1,149 models declare themselves as their own parent |
| parents missing from the dump become `:Unlisted` nodes | 22% of lineage edges point to a model that is not public anymore |
| ignore arXiv `1910.09700` | cited by 126k models only because it is in the default card template |
| precompute depth, descendants, roots | shown on every model page; 99.6% of chains are <= 5 levels deep |

> On Windows, put `MODELATLAS_DATA_DIR=C:\modelatlas-data` in `.env` so the 1.5 GB download is not synced by OneDrive.

---

## Graph model

| Nodes | Key | Relationships (from `Model`) |
| --- | --- | --- |
| `Model` (+ `Unlisted` label for parents not in the dump) | `id` | `FINETUNED_FROM`, `ADAPTER_OF`, `QUANTIZED_FROM`, `MERGED_FROM` → `Model` |
| `Organization` | `id` | `PUBLISHED_BY` |
| `License` (with `family`, `commercial_use` rules) | `id` | `HAS_LICENSE` |
| `Dataset` | `id` | `TRAINED_ON` |
| `Paper` | `arxiv_id` | `CITES` |
| `Task` | `id` | `PERFORMS` |
| `Language` | `code` | `SUPPORTS` |
| `Architecture` | `id` | `HAS_ARCHITECTURE` |

8 uniqueness constraints, 7 range indexes and 1 fulltext index, all in [`app/database/schema.py`](app/database/schema.py)
with the query each one serves. Full diagram and modeling rationale: coming in `docs/`.

---

## Architecture

Six services run in `docker compose`; only Nginx is published on the network.

| Service | Image | Port | Role |
| --- | --- | --- | --- |
| `nginx` | `nginx:1.27-alpine` | 80 (public) | Reverse proxy: `/api` → API, `/` → UI, rate limit on the agent |
| `api` | `docker/api.Dockerfile` | 8000 (internal) | FastAPI, graph queries, agent, guardrails |
| `ui` | `docker/ui.Dockerfile` | 8501 (internal) | Streamlit web app |
| `neo4j` | `neo4j:5.26-community` + APOC + GDS | 7474 / 7687 (localhost only) | The graph |
| `ollama` | `ollama/ollama` | 11434 (localhost only) | Local LLM, no API key |

The UI never talks to Neo4j directly: every query goes through the API, which is where validation,
guardrails and evidence labels live. (Diagram coming with the graph model PR.)

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
| `make data` | Download the raw Hugging Face model-card dump (~1.5 GB, once) |
| `make profile` | Profile the raw dump into `reports/data_profile.md` |
| `make fixture` | Rebuild the small test fixture from the full dump |
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
tests/          unit/, api/, health/ (+ cypher/ and agent/ later)
docker/         Dockerfiles and Nginx config
config/         versioned config (data sources, license rules, scope)
.github/        CI workflows, PR template
```

---

## Team

| Member | GitHub |
| --- | --- |
| Marius Rang (lead) | [@mariusrang](https://github.com/mariusrang) |
| Ylias Guetari | [@ylguet](https://github.com/ylguet) |
| Lucas Magret Delord | [@Lucasmd9](https://github.com/Lucasmd9) |

---

## Disclaimer

ModelAtlas is a student project for educational purposes. License analysis is informational only and is
**not legal advice**. Model metadata on Hugging Face is self-declared by uploaders and can be wrong; we show
where every fact comes from so you can check it.
