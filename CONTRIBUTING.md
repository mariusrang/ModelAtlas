# Contributing to ModelAtlas

## Branches

- `main` is the stable branch: protected, only updated through Pull Requests with green CI.
- Work on a feature branch named `<type>/<short-name>`, for example `feat/etl-tag-parser` or `fix/health-timeout`.

## Commits

We use [Conventional Commits](https://www.conventionalcommits.org/) prefixes:

| Prefix | Use it for |
| --- | --- |
| `feat:` | a new feature (endpoint, ETL step, UI page...) |
| `fix:` | a bug fix |
| `test:` | adding or fixing tests only |
| `docs:` | README, report, diagrams, docstrings |
| `refactor:` | code change with no behaviour change |
| `chore:` | tooling, CI, dependencies, config |

Keep commits small and the message in the imperative: `feat: add license-check endpoint`.

## Pull Requests

1. `make format && make lint && make test` before pushing.
2. Open the PR against `main` and fill in the template.
3. Every PR gets a review with **at least 2 constructive comments** before merge.
4. Squash or merge once CI is green.

## Local setup

```bash
make install        # .venv + dependencies
make env            # creates .env from .env.example
make docker-run     # whole stack on http://localhost
```
