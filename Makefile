# ModelAtlas - developer commands.  Run `make help` to see them all.
# Works on Linux/macOS and on Windows (Git Bash or any shell with GNU make).

.DEFAULT_GOAL := help
.PHONY: help venv install run ui env docker-build docker-run docker-down docker-logs ollama-pull data data-pin profile fixture seed seed-sample gds gds-sample predict predict-sample agent-eval \
        test test-unit test-integration coverage lint format clean tree

# --- Python paths (the venv layout differs on Windows) ---
ifeq ($(OS),Windows_NT)
SYS_PY ?= python
# backslashes: without Git Bash, make runs recipes through cmd.exe, which
# misreads forward slashes in a program path as command switches
VENV_PY := .venv\Scripts\python.exe
else
SYS_PY ?= python3
VENV_PY := .venv/bin/python
endif

PY_SOURCES := app ui scripts tests
# read and write files as UTF-8 on Windows too (isort could not parse files with emojis in cp1252)
export PYTHONUTF8 := 1
OLLAMA_MODEL ?= qwen2.5:7b-instruct
# `make ui` on your machine talks to the API behind Nginx (docker stack); override for `make run`
export API_BASE_URL ?= http://localhost/api

help: ## Show this help
	@$(SYS_PY) scripts/dev.py help

venv: ## Create the local virtualenv (.venv) if missing
	@$(SYS_PY) scripts/dev.py venv

install: venv ## Install runtime + dev dependencies into .venv
	$(VENV_PY) -m pip install --upgrade pip
	$(VENV_PY) -m pip install -r requirements-dev.txt

env: ## Create .env from .env.example if you don't have one yet
	@$(SYS_PY) scripts/dev.py env

# the API on your machine reaches Neo4j and Ollama through their published ports, whatever .env says
# (an old .env may still have the docker service names neo4j / ollama)
run: export NEO4J_URI = bolt://localhost:7687
run: export OLLAMA_BASE_URL = http://localhost:11434
run: ## Run the API locally with auto-reload on http://localhost:8000 (needs `make docker-run` for Neo4j)
	$(VENV_PY) -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

ui: ## Run the Streamlit UI locally on http://localhost:8501 (API_BASE_URL defaults to the docker stack)
	$(VENV_PY) -m streamlit run ui/app.py

docker-build: ## Build the api and ui Docker images
	docker compose build

docker-run: env ## Build and start the whole stack (Neo4j, Ollama, API, UI, Nginx) on http://localhost
	docker compose up --build -d
	@docker compose ps
	@echo "UI:      http://localhost"
	@echo "API:     http://localhost/api/docs"
	@echo "Neo4j:   http://localhost:7474"

docker-down: ## Stop the stack (data volumes are kept)
	docker compose down

docker-logs: ## Follow the logs of every service
	docker compose logs -f --tail=100

ollama-pull: ## Download the local LLM into the Ollama container (once, ~4.7 GB)
	docker compose exec ollama ollama pull $(OLLAMA_MODEL)

data: ## Download both raw dumps: model cards + dataset cards (~2.5 GB, only once)
	$(VENV_PY) -m scripts.etl.extract

data-pin: ## Pin config/sources.toml to the exact commits downloaded by `make data`
	$(VENV_PY) -m scripts.etl.extract --pin

profile: ## Profile the raw dump -> reports/data_profile.md
	$(VENV_PY) -m scripts.etl.profile

fixture: ## Rebuild the small test fixtures (tests/fixtures/sample_*) from the full dumps
	$(VENV_PY) -m scripts.etl.make_fixture

seed: ## Build the FULL graph in Neo4j: transform + load + validate (~10 min, stack must be up)
	$(VENV_PY) -m scripts.seed_data

seed-sample: ## Load the small fixture into Neo4j (~1 min, used by CI and the Cypher tests)
	$(VENV_PY) -m scripts.seed_data --sample

gds: ## Run the graph algorithms (PageRank, WCC, Louvain, Node Similarity, FastRP) -> reports/gds_report.md
	$(VENV_PY) -m scripts.graph_algorithms

gds-sample: ## Same on the sample graph (used by CI) -> reports/gds_report_sample.md
	$(VENV_PY) -m scripts.graph_algorithms --sample

predict: ## Predict missing parents (link prediction, needs `make gds`) -> reports/link_prediction.md
	$(VENV_PY) -m scripts.link_prediction

predict-sample: ## Same on the sample graph (used by CI) -> reports/link_prediction_sample.md
	$(VENV_PY) -m scripts.link_prediction --sample

EVAL_REPORT ?= agent_eval.md
agent-eval: ## Run the agent on config/agent_eval.toml (needs the stack + make ollama-pull) -> reports/$(EVAL_REPORT)
	$(VENV_PY) -m scripts.evaluate_agent --output $(EVAL_REPORT)

.PHONY: bench
bench: ## Benchmark naive vs optimized Cypher queries -> reports/benchmarks.md
	$(VENV_PY) -m scripts.benchmark

test: ## Run the whole test suite with coverage (integration tests skip if the stack is down)
	$(VENV_PY) -m pytest --cov=app --cov=ui --cov=scripts --cov-report=term-missing --cov-report=xml $(PYTEST_ARGS)

test-unit: ## Run only fast tests (no Neo4j needed)
	$(VENV_PY) -m pytest -m "not integration"

test-integration: ## Run tests that need the running stack (make docker-run first)
	$(VENV_PY) -m pytest -m integration

coverage: ## Build an HTML coverage report in htmlcov/
	$(VENV_PY) -m pytest -m "not integration" --cov=app --cov=ui --cov=scripts --cov-report=html
	@echo "open htmlcov/index.html"

lint: ## Check formatting and run pylint with the course .pylintrc (must be >= 9.5)
	$(VENV_PY) -m black --check $(PY_SOURCES)
	$(VENV_PY) -m isort --check-only $(PY_SOURCES)
	$(VENV_PY) -m pylint --rcfile=.pylintrc --fail-under=9.5 --reports=n $(PY_SOURCES)

format: ## Auto-format the code with black and isort
	$(VENV_PY) -m isort $(PY_SOURCES)
	$(VENV_PY) -m black $(PY_SOURCES)

clean: ## Remove caches, coverage files and build leftovers
	@$(SYS_PY) scripts/dev.py clean

tree: ## Show the project tree (depth 3)
	@$(SYS_PY) scripts/dev.py tree
