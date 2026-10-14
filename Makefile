# ModelAtlas - developer commands.  Run `make help` to see them all.
# Works on Linux/macOS and on Windows (Git Bash or any shell with GNU make).

.DEFAULT_GOAL := help
.PHONY: help venv install run ui env docker-build docker-run docker-down docker-logs ollama-pull data profile fixture seed seed-sample \
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
OLLAMA_MODEL ?= qwen2.5:7b-instruct

help: ## Show this help
	@$(SYS_PY) scripts/dev.py help

venv: ## Create the local virtualenv (.venv) if missing
	@$(SYS_PY) scripts/dev.py venv

install: venv ## Install runtime + dev dependencies into .venv
	$(VENV_PY) -m pip install --upgrade pip
	$(VENV_PY) -m pip install -r requirements-dev.txt

env: ## Create .env from .env.example if you don't have one yet
	@$(SYS_PY) scripts/dev.py env

run: ## Run the API locally with auto-reload on http://localhost:8000 (needs Neo4j up)
	$(VENV_PY) -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

ui: ## Run the Streamlit UI locally on http://localhost:8501
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

data: ## Download the raw Hugging Face model-card dump (~1.5 GB, only once)
	$(VENV_PY) -m scripts.etl.extract model_cards

profile: ## Profile the raw dump -> reports/data_profile.md
	$(VENV_PY) -m scripts.etl.profile

fixture: ## Rebuild the small test fixture (tests/fixtures/sample_models) from the full dump
	$(VENV_PY) -m scripts.etl.make_fixture

seed: ## Build the FULL graph in Neo4j: transform + load + validate (~10 min, stack must be up)
	$(VENV_PY) -m scripts.seed_data

seed-sample: ## Load the small fixture into Neo4j (~1 min, used by CI and the Cypher tests)
	$(VENV_PY) -m scripts.seed_data --sample

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
