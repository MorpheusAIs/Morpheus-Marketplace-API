# Issue tracker

A change is done when it is on `main` and production has deployed. The issue stays open through `dev` and `test`.

Maintainers follow the full practice in the private repo `MorpheusAIs/Morpheus-Maintainer-Context`, file `.ai-docs/how-we-work.md`. This page is the local rules for this repository.

## Filing

Use the issue forms:

- **Bug report** for something broken
- **Feature request** for a change or a proposal
- **Docs** for a wrong or missing page on apidocs.mor.org or in this repository

Questions are not tracked as issues. The API docs are at [apidocs.mor.org](https://apidocs.mor.org). Blank issues are closed.

Security issues are never a public issue. Report them as [SECURITY.md](../SECURITY.md) describes.

A new issue is labeled `needs-triage`. During triage, set one area label and the Priority field, set the status on the **Morpheus Work** project, then clear `needs-triage`.

Area labels: `area: inference`, `area: models`, `area: sessions`, `area: billing`, `area: auth-keys`, `area: docs`.

Priority is the GitHub field in the issue sidebar: **Urgent** now, **High** next, **Medium** backlog, **Low** someday. The issue form sets Type (Bug, Feature, Task). Effort is optional.

## Pull requests

Feature, docs, and fix pull requests target `dev`. A promote pull request targets `test` or `main` and its title starts with `promote:`. A release pull request targets `main` and its title starts with `Release`.

The body contains a line `Refs #123`, or the checked no-issue line for a chore, revert, or CI-only change.

Promote pull requests are merge commits.

When this work is part of an initiative, create the issue as a sub-issue of that initiative:

```shell
gh issue create --repo MorpheusAIs/Morpheus-Marketplace-API \
  --title "TITLE" --body "BODY" \
  --parent https://github.com/MorpheusAIs/Morpheus-Maintainer-Context/issues/N
```

## Status

The public project **Morpheus work** holds issues from this repo. Status for this repo:

Triage → Needs Info → Backlog → Ready → In Progress → On Dev → On Test → Shipped

The branch named `test` deploys the AWS DEV environment, so **On Test** means the change is live on api.dev.mor.org.

Nothing is promoted to `stg`. A pull request into `stg` fails the **PR target** check.

Shipped means the commit is on `main`. The workflow comments on the linked issue at each hop and closes it on `main`.

`Refs #` on the **pull request body** are authoritative. Commit messages may also carry `Refs #` for a single-commit fix, but promote/Release commit messages are ignored so a retargeted Dependabot PR (branch cut from `main`, base changed to `dev`) cannot re-process historical Refs. Landing on `dev` or `test` never reopens a closed issue.

## The workflow and the build

`.github/workflows/issue-tracker.yml` runs on pull request events only. It does not build or deploy.

A push that changes any file under `.github/` matches the path filter of the build workflow, so a change to the tracker files runs the build for the branch it lands on, like a code change.

## Setup checklist

In the repository:

- [x] Issue forms for bug, feature, and docs
- [x] Pull request template asks for `Refs #` or the no-issue line
- [x] `CONTRIBUTING.md` states that feature pull requests target `dev`
- [x] Workflow fails a pull request that does not target `dev`, unless it is a promote or a release
- [x] Workflow comments on the linked issue and closes it only on `main`
- [x] Labels created: `needs-triage`, `needs-info`, `needs-issue`, and the `area:` labels
- [x] Public project **Morpheus Work** is https://github.com/orgs/MorpheusAIs/projects/3
- [x] Repository variable `MORPHEUS_WORK_PROJECT` is `3`
- [x] Organization secret `MORPHEUS_WORK_TOKEN` is granted to this repository. It is a fine-grained token with organization Projects read and write, and repository Issues and Pull requests read and write. Without it the workflow still comments and closes, and skips the status move.
- [ ] The project's auto-add workflow includes this repository, so a new issue arrives in Triage
- [ ] Open issues added to the project in Triage
- [ ] After this workflow is on `dev`, the **PR target** check is required on `dev`, `test`, and `main`
- [ ] Later, the missing-issue reminder becomes a required check
