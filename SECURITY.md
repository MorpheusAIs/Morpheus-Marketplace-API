# Security Policy

## Reporting a vulnerability

Report suspected vulnerabilities in this repository or in the hosted Morpheus Marketplace API privately.

Email [security@mor.org](mailto:security@mor.org).

Do not open a public GitHub issue, pull request, or discussion for a suspected vulnerability. Do not include live credentials, customer data, or proof-of-concept traffic against production.

Please include:

- What is affected (host, endpoint, version, or commit)
- Impact
- Steps to reproduce, or a minimal description of the issue
- Whether you have already seen it used against the service

We will acknowledge the report and follow up with a fix path. Please give us time to investigate before any public write-up.

## Scope

In scope:

- This repository
- The hosted API it deploys: `api.mor.org`, `api.stg.mor.org`, and `api.dev.mor.org`

Out of scope (report those to their own projects):

- Third-party model providers
- Morpheus smart contracts and the proxy-router node ([Morpheus-Lumerin-Node](https://github.com/MorpheusAIs/Morpheus-Lumerin-Node))
- The web app ([Morpheus-Marketplace-APP](https://github.com/MorpheusAIs/Morpheus-Marketplace-APP))

## Supported versions

Security fixes land on `dev`, then maintainers promote them through `test` and `stg` to `main`. Production is the `main` image. Older image tags are not maintained as separate release lines.
