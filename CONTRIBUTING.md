# Contributing to Morpheus Marketplace API

Thanks for contributing. This repository promotes through long-lived branches. A push to the wrong branch deploys the hosted API, so read the branch table before you open a pull request.

## Branch model

| Branch | Role | What a code push does |
|--------|------|------------------------|
| feature / fix | Your work | Nothing. Open a pull request into `dev`. |
| **`dev`** | Integration. Default PR base. | Tests only. No image push, no deploy. |
| **`test`** | AWS **DEV** environment | Build, push, deploy to [api.dev.mor.org](https://api.dev.mor.org). |
| **`main`** | Production | Build, push, deploy to [api.mor.org](https://api.mor.org), tag `:latest`. |
| `cicd/*` | Maintainer fast-cycle | Deploys to the AWS DEV environment. Do not use for normal work. |

```
you → [PR] → dev  →  [promote] → test  →  main
```

The branch named `test` deploys the AWS DEV environment. Production is `main` only.

### Default PR base: `dev`

- Target **`dev`**. Do not open feature pull requests against `test` or `main`.
- Only maintainers promote `dev` → `test` → `main`. Nothing is promoted to `stg`.
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

Promote and release pull requests are opened by maintainers:

- Promote into `test`: `promote: <summary> (dev -> test)`
- Release into `main`: `Release vX.Y.Z — <themes>`

## Issues

File a bug, a feature request, or a docs correction with the issue forms. Questions are not tracked as issues; the API docs are at [apidocs.mor.org](https://apidocs.mor.org).

An issue closes when the change is on `main` and production has deployed. The local rules and the setup checklist are in [`.ai-docs/issue-tracker.md`](.ai-docs/issue-tracker.md). Maintainers follow the practice in `Morpheus-Maintainer-Context` (`.ai-docs/how-we-work.md`).

A feature or fix pull request targets `dev`. The body contains `Refs #123`, or the no-issue line for a chore, revert, or CI-only change. A promote pull request targets `test` or `main` and its title starts with `promote:`. A release pull request targets `main` and its title starts with `Release`. The **PR target** check fails any other pull request that does not target `dev`.

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

The workflow in `.github/workflows/build.yml` runs on push to `dev`, `test`, `main`, and `cicd/*` only when the push touches one of:

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
