# Contributing to Morpheus Marketplace API

Thanks for contributing. This repository promotes through long-lived branches. A push to the wrong branch deploys the hosted API, so read the branch table before you open a pull request.

## Branch model

| Branch | Role | What a code push does |
|--------|------|------------------------|
| feature / fix | Your work | Nothing. Open a pull request into `dev`. |
| **`dev`** | Integration. Default PR base. | Tests only. No image push, no deploy. |
| **`test`** | AWS **DEV** environment | Build, push, deploy to [api.dev.mor.org](https://api.dev.mor.org). |
| **`stg`** | Staging | Build, push, deploy to [api.stg.mor.org](https://api.stg.mor.org). |
| **`main`** | Production | Build, push, deploy to [api.mor.org](https://api.mor.org), tag `:latest`. |
| `cicd/*` | Maintainer fast-cycle | Deploys to the AWS DEV environment. Do not use for normal work. |

```
you → [PR] → dev  →  [promote] → test  →  stg  →  main
```

The branch named `test` deploys the AWS DEV environment. Production is `main` only.

### Default PR base: `dev`

- Target **`dev`**. Do not open feature pull requests against `test`, `stg`, or `main`.
- Only maintainers promote `dev` → `test` → `stg` → `main`.
- If GitHub suggests `main` as the base, change it to `dev` before review.

## How to submit a change

1. Branch from current `dev`:
   ```bash
   git fetch origin
   git checkout -b fix/your-topic origin/dev
   ```
2. Keep the change focused.
3. Open a pull request with base `dev`.
4. If review sits for a while, merge current `origin/dev` into your branch.

Prefer commit titles with a conventional prefix: `feat:`, `fix:`, `docs:`, `chore:`.

## Local checks

Python 3.11 and Poetry.

```bash
poetry install
poetry run ruff format
poetry run ruff check
poetry run mypy
poetry run pytest
```

Local API without external dependencies: `./scripts/test_local.sh` (see the README quick start).

## What does not start a build

The workflow in `.github/workflows/build.yml` runs on push to `dev`, `test`, `stg`, `main`, and `cicd/*` only when the push touches one of:

- `.github/**`
- `src/**`
- `alembic/**`
- `tests/**`
- `pyproject.toml`, `poetry.lock`, `Dockerfile`, `alembic.ini`

Documentation and repo-health files do **not** match that filter, so they do not test, build, or deploy any environment. Keep them out of `.github/` (a file added there would match the filter):

- `LICENSE`, `CONTRIBUTING.md`, `SECURITY.md`, `README.md`
- `.ai-docs/**`
- `.gitignore`

A mixed push that also changes a filtered path still runs the pipeline for that branch.

## Review expectations

- No secrets in the pull request (`.env`, private keys, API keys, production data).
- Say what you tested.
- Report vulnerabilities through [SECURITY.md](SECURITY.md), not a public issue.
