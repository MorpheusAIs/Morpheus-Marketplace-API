# Security Fixes — Progress Tracker

Working branch: `security/backend-fixes` (based on `dev`) — ongoing; multiple
fixes land here.

Tracks remediation of backend (`BE-*` / `B-*`) findings from
[`ATTACK_SCENARIOS.md`](ATTACK_SCENARIOS.md). Each entry links the scenario, the
underlying finding IDs, the code touched, and the current state.

Status legend: **Done** · **In progress** · **Todo** · **Accepted** (risk
accepted, no fix) · **Won't fix** (with reason)

---

## Done

### BE-1 — Auth master off-switch — Critical (config-dependent)

- **Findings:** B-08 (`LOCAL_TESTING_MODE` + `BYPASS_COGNITO_AUTH` read from raw
  env with no production guard), B-20 (ops hygiene: dev env files ship both flags
  `true`).
- **Risk:** If both flags reached a production deploy (copied `.env`,
  task-definition drift, wrong deploy profile), all three auth dependencies
  (`get_current_user`, `get_api_key_auth`, `get_user_jwt_or_api_key`) returned a
  shared anonymous test user. Every account accessible to everyone; only a single
  startup log line warned.
- **Fix:** `src/core/local_testing.py` — `is_local_testing_mode()` now consults
  `ENVIRONMENT` and raises `AuthBypassMisconfiguration` when the bypass is
  requested outside an explicit local/dev/test environment (fail-closed
  allowlist: `development`, `dev`, `test`, `local`). `log_local_testing_status()`
  runs at startup, so a misconfigured prod/staging deploy now **crashes on boot**
  with a critical log line instead of silently disabling auth.
- **Verified:** bypass active only in dev/test; raises in production / staging /
  unknown env; unaffected when flags off or only one flag set.
- **Commit:** `9bbf467`
- **Follow-ups (not code, out of this commit):**
  - B-20: stop shipping example env files with both flags `true`.
  - B-20: deploy tooling should not default to the prod AWS profile with these
    flags set; consider a CI check that rejects the pair in prod manifests.

### BE-2 — Free inference via overdraw / void tricks — High

Verified against current code (post-`dev` merge). The three sub-findings were
**not** equally true; billing had been reworked (FOR UPDATE locking, staking/paid
split, shielded cleanup), so we split them:

- **B-06 (disconnect voids hold despite partial delivery) — FIXED.** This was the
  real, repeatable free-inference primitive: streaming billing keyed finalize-vs-void
  on whether the provider's *final* `usage` chunk arrived, so a client that
  disconnected just before it got the whole answer and the hold was voided (zero
  charge). A `$1` balance funded unlimited free streaming. Also covered the
  "stream completed but provider sent no usage chunk → `$0` finalize" sub-case.
  - **Fix:** `src/api/v1/chat/chat_streaming.py` — `StreamingUsageAccumulator` now
    measures assistant content actually delivered (`note_delivered_chunk`, buffered
    across raw byte reads) and records whether the authoritative provider usage
    chunk was seen. Cleanup now **finalizes whenever output was delivered** (clean
    completion *or* disconnect-with-content) and **voids only when nothing billable
    reached the client**. `_finalize_streaming_billing` bills provider usage when
    present, else falls back to measured output tokens + the request's input
    estimate.
  - **Verified:** isolated logic test (finalize-on-disconnect-with-content,
    provider-usage-on-clean-completion, void-on-nothing-delivered) + all 7
    `test_chat_streaming_failover.py` tests pass.
- **B-04 (finalize uncapped vs hold, no negative-balance floor) — ACCEPTED, no fix.**
  Balance can go negative by roughly one request's overflow (e.g. omit `max_tokens`
  so the hold estimate caps at 2048 output tokens, then force a longer generation).
  Bounded per account: once negative, the next hold's sufficiency check rejects it.
  Product decision: acceptable — the next request is blocked, so no runaway abuse.
- **B-05 (stale reaper voids in-flight holds; finalize resurrects `voided`) —
  NOT NEEDED (evaluated after B-06).** Practically unreachable: a streaming hold is
  `pending` only until cleanup (≤ ~180s, the proxy read timeout), while the reaper
  only voids holds pending > `HOLD_MAX_PENDING_SECONDS` (3600s) — a ~20× gap. The
  only holds the reaper voids are genuinely orphaned (crashed worker, no finalize
  coming), so the finalize-resurrects-`voided` path never fires. Even if it did,
  `finalize` *charges* the actual cost (does not refund), so there is no revenue
  loss — only a cosmetically-wrong ledger row. B-06 neither creates nor worsens any
  reaper interaction.
  - **Optional future hardening (defense-in-depth, not required):** make
    `finalize_usage` return early on `status == voided`, and add a startup assertion
    that `HOLD_MAX_PENDING_SECONDS > proxy_timeout` so the safety gap can't be
    misconfigured away.
- **Commit:** `fd57759`

### BE-6 / B-10 — Deactivation doesn't revoke — Medium — FIXED UPSTREAM (in `dev`)

- **Finding:** B-10 (JWT path never checked `is_active`; 600s user cache delayed
  revocation).
- **Status:** Already fixed on `dev` (merged into this branch); no change needed
  here. Verified against current code:
  - `get_current_user` now rejects inactive users and clears their cache entry
    (`src/dependencies.py:228-240`), and also rejects tombstoned/deleted
    identities on the JWT path (`:159-170`).
  - Both halves of the recommended fix are present: the `is_active` check and
    cache invalidation on the rejection path.
- **Residual (minor, not a live vuln):** the 600s user cache still means a
  *manual* DB flip of `is_active=False` (bypassing app code) isn't reflected until
  the cache expires. There is no in-app "deactivate user" endpoint — removal goes
  through `delete_user`, which tombstones + clears the cache (`crud/user.py:151`),
  and the existing admin mutation path clears the cache too (`admin.py:344`). If a
  deactivate endpoint is ever added, it must call
  `cache_service.delete("user", cognito_user_id)` like the others.

---

## Todo (backend, by priority)

Order follows §5 of `ATTACK_SCENARIOS.md` ("if you only fix ten things").

### CB-1 / B-01 — Idempotency key on credit adjust — Critical (latent)

- **Findings:** B-01 (`adjust:{user}:{now}:{uuid4()}` random idempotency key
  never dedupes replays), F-01, F-02.
- **Fix direction:** derive a deterministic key (`stripe:{session.id}`) so
  replays collide on the unique constraint. **Do B-01 before repairing the
  currently-401'd frontend credit path**, or the latent Critical goes live.
- **Status:** Todo.

### CB-2 / B-02 — Single canonical Stripe webhook — High

- **Findings:** B-02c (frontend + backend webhook implementations, disjoint
  idempotency namespaces → double-credit).
- **Fix direction:** keep the backend webhook as canonical, delete the frontend
  path, alert on error streaks.
- **Status:** Todo.

### BE-7 / B-12, B-19 — Encryption key default — Critical (config-dependent)

- **Findings:** B-12 (`ENCRYPTION_SECRET_KEY` defaults to
  `encryption_secret_change_me`), B-19 (AES-CBC, no integrity).
- **Fix direction:** no default — fail startup when unset; migrate to AES-GCM;
  rotation audit. (Same startup-hard-fail pattern as BE-1.)
- **Status:** Todo.

### BE-3 / B-09 — Legacy API key prefix-only auth — High

- **Findings:** B-09 (`encrypted_key IS NULL` rows authenticate on 9-char prefix,
  no hash check).
- **Fix direction:** reject prefix-only rows; force-rotate legacy keys.
- **Status:** Todo.

### BE-4 / B-17 — Signup-bonus IP spoofing — Medium

- **Findings:** B-17 (first `X-Forwarded-For` hop trusted verbatim), B-13, B-15.
- **Fix direction:** trusted-proxy IP resolution.
- **Status:** Todo.

### BE-5 / B-03 — Payment-link BOLA — Medium

- **Findings:** B-03 (payment-link GET not token-scoped).
- **Fix direction:** scope lookup to `metadata.user_id == current_user`, 404 on
  mismatch. Also closes FE-3 one layer down.
- **Status:** Todo.

### CB-4 / B-13 — Credentialed CORS reflects any HTTPS origin — High (trap)

- **Findings:** B-13 (`Access-Control-Allow-Credentials: true` with any-HTTPS
  origin reflection). Latent while auth is Bearer; becomes CSRF the moment tokens
  move to cookies (F-07).
- **Fix direction:** `allow_direct_access=False` in prod + explicit origin
  allowlist. Do **before/with** any cookie-auth migration.
- **Status:** Todo.

### B-14 — Stop logging request bodies — Low (blast-radius)

- **Fix direction:** redact/stop logging request bodies; limits every other
  scenario's disclosure surface.
- **Status:** Todo.

---

## Out of scope here (frontend / other repo)

`FE-*` findings live in `Morpheus-Marketplace-APP`. Combined scenarios (CB-*)
have both a backend and a frontend half — the backend halves are tracked above.
