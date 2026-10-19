# Contributing to ModelAtlas

## Branches

- `main` is the stable branch: protected (GitHub *Settings -> Branches*: require a pull request,
  1 approving review and green `lint` / `tests` checks), only updated through Pull Requests.
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
One commit = one logical change that still passes `make lint` and `make test-unit`
(e.g. "add the query", then "add the endpoint", then "add the tests"), not one commit per PR.
Commit with your own GitHub identity (`git config user.name` / `user.email`), otherwise
your work is not counted for you.

## Pull Requests

1. `make format && make lint && make test` before pushing.
2. Open the PR against `main` and fill in the template.
3. Every PR is reviewed by **another member**, with **at least 2 constructive comments**
   (a question on a choice, a missing test, a clearer name...). The author answers or fixes each one.
4. The reviewer approves, then merge with **"Create a merge commit"** once CI is green
   (not squash: it would hide the individual commits).

## Local setup

```bash
make install        # .venv + dependencies
make env            # creates .env from .env.example
make docker-run     # whole stack on http://localhost
```
