# Security Analysis — Morpheus Marketplace API (Backend)

**Date:** 2026-07-23
**Scope:** This repository (`Morpheus-Marketplace-API`) — FastAPI backend on branch `dev` @ `0b9e275c`: Cognito JWT + API-key auth, credits ledger/billing, Stripe & Coinbase webhook crediting, inference hold/capture pipeline, infra/config/CI.
**Trigger:** Follow-up to `Morpheus-Marketplace-APP/SECURITY_ANALYSIS.md` (frontend, 20 findings), which deferred: *"idempotent credit adjustment and token-scoped lookups need a separate audit there."* Both items are covered here (B-01, B-03) along with a full backend sweep.
**Frameworks applied:** OWASP Top 10 (2021), OWASP API Security Top 10 (2023), CWE, ASVS 4.0 (selected).
**Method:** Static source review of all routers, services, CRUD, models, middleware, config, Dockerfile, CI. Headline subagent findings were independently re-verified against source before inclusion. No dynamic testing.

---

## 1. Executive Summary

The backend's **core payment-receiving machinery is good**: both webhook handlers verify signatures, fail closed on missing secrets, and credit idempotently via a two-level check (event ID + transaction ID) backed by a unique constraint on `credits_ledger.idempotency_key`. The ledger itself uses atomic SQL arithmetic and row locking correctly.

**The problems are at the seams:**

1. **The admin credit-adjustment endpoint is not idempotent** — it generates a random idempotency key per call, so the unique constraint never fires. This is the backend half of the frontend's F-01: any replayed/duplicated credit request via `/billing/credits/adjust` always re-credits.
2. **The Stripe top-up pipeline appears broken end-to-end in two independent ways** (static analysis; needs runtime confirmation): the frontend webhook's credit call carries no Bearer token and must 401 against this backend, and the frontend checkout writes `metadata.userId` while this backend's webhook reads `metadata.user_id`. Additionally two parallel Stripe webhook implementations exist with disjoint idempotency namespaces — if both are configured in Stripe, every payment double-credits.
3. **The inference billing pipeline can be driven to negative balances and free usage** — capture can exceed the hold, the stale-hold reaper can void in-flight holds, and several paths finalize at $0.
4. **Auth has a full bypass switch** (`LOCAL_TESTING_MODE` + `BYPASS_COGNITO_AUTH`, raw env, no production guard) and **legacy API keys authenticate on key prefix alone**.
5. **Config fails open**: hardcoded fallback encryption key, hardcoded production Cognito pool defaults, CORS that allows any HTTPS origin with credentials, rate limiting that disables itself when Redis is down, and full chat request bodies written to INFO logs.

**Positives (verified clean):** no SQL injection surface (ORM + bound params throughout), webhook signature handling (Stripe SDK + HMAC/`compare_digest` + 5-min replay window), admin secret compared with `secrets.compare_digest` and fails closed (503) when unset, BOLA-free user scoping on chat history / wallets / API keys / billing reads, EIP-191 signature verification on wallet linking, signup bonus double-guarded (per-user idempotency key + per-IP window), no committed secrets, non-root multi-stage Dockerfile, stale-hold reaper uses an advisory lock against multi-replica double-voids.

---

## 2. Findings Summary

| # | Severity | Finding | Primary mapping |
|---|----------|---------|-----------------|
| B-01 | **High** | `/billing/credits/adjust` not idempotent (random key per call) — backend half of APP F-01 | API6:2023, CWE-770 |
| B-02 | **High** | Stripe crediting broken/dangerous end-to-end: 401-ing credit path, `userId`/`user_id` metadata mismatch, dual webhook implementations | A04:2021, API6:2023 |
| B-03 | **Medium** | `GET /coinbase/payment-links/{id}` authenticated but not token-scoped (BOLA) — backend half of APP F-06 | API1:2023 |
| B-04 | **High** | Finalize can capture more than hold → negative balance / overdraw | CWE-770, business logic |
| B-05 | **High** | Stale-hold reaper voids in-flight holds; finalize ignores `voided` → free usage | CWE-841 |
| B-06 | **Medium** | Billing loss paths: disconnect voids hold, finalize errors leave pending holds, missing usage finalizes at $0 | CWE-754 |
| B-07 | **Medium** | `create_refund` trusts caller amount; idempotency keyed by `reason` (no HTTP caller today) | CWE-770 |
| B-08 | **Critical** | Local-testing mode bypasses all auth; no environment guard | A07:2021, CWE-489 |
| B-09 | **High** | Legacy API keys authenticate on 9-char prefix only (no secret verification) | A07:2021, CWE-287 |
| B-10 | **Medium** | JWT path never checks `user.is_active` (deactivated users retain access) | A07:2021 |
| B-11 | **Medium** | Public `/exchange-token` mints/returns tokens; auth code in GET query string | A07:2021, CWE-598 |
| B-12 | **Critical** | `ENCRYPTION_SECRET_KEY` defaults to `encryption_secret_change_me` | A02:2021, CWE-798 |
| B-13 | **High** | CORS allows any HTTPS origin with credentials (`allow_direct_access=True`) | A05:2021, CWE-942 |
| B-14 | **High** | Full chat request bodies (prompts, tool args) logged at INFO | CWE-532 |
| B-15 | **High** | Rate limiting fails open when Redis unavailable | API4:2023 |
| B-16 | **High** | Hardcoded production Cognito pool/client defaults in config | A05:2021, CWE-1188 |
| B-17 | **Medium** | Signup-bonus IP guard trusts spoofable `X-Forwarded-For` | CWE-348 |
| B-18 | **Medium** | Internal error strings (`str(e)`) returned in 500 responses across auth + billing | CWE-209 |
| B-19 | **Medium** | AES-CBC without authenticated encryption for stored API keys | A02:2021, CWE-353 |
| B-20 | **Medium** | Ops hygiene: DB schema check disabled, CI rewrites `alembic_version`, non-blocking tests, unpinned actions, public debug endpoints | A05/A08:2021 |
| B-21 | **Low** | No `token_use` validation; non-constant-time API-key hash compare | A07:2021 |
| B-22 | **Low** | `python-jose` unmaintained (CVE-2024-33663/33664; low exploitability here) | A06:2021 |
| B-23 | **Info** | Audio endpoints unbilled; `s3_presigned_url` forwarded to proxy-router (delegated SSRF) | Business logic, API7:2023 |

---

## 3. Detailed Findings — Payment Integrity (the deferred frontend items)

### B-01 — HIGH — `/billing/credits/adjust` is not idempotent (backend half of APP F-01)

**Evidence:**
- `src/api/v1/billing/admin.py:189-260` — `POST /credits/adjust` (requires `get_current_user` **and** `X-Admin-Secret`) calls `billing_service.adjust_credits(...)`.
- `src/services/billing_service.py:711`:

```python
idempotency_key = f"adjust:{user_id}:{datetime.utcnow().isoformat()}:{uuid.uuid4()}"
```

A fresh UUID per call → the `unique=True` constraint on `credits_ledger.idempotency_key` (`src/db/models/credits.py:49`) can never dedupe an adjustment. The endpoint accepts no client-supplied idempotency key or external transaction ID; the Stripe session ID passed by the frontend is only embedded in the free-text `description`.

**Impact:** Any retry, double-submission, or replay of a credit request through this endpoint re-credits unconditionally. Combined with the frontend's F-01 (its Stripe webhook has no idempotency), a replayed signed Stripe event credits N times — **neither layer stops it**. This is exactly the "idempotent credit adjustment" the frontend audit deferred here.

**Fix:**
1. Accept an explicit `idempotency_key` (or `external_transaction_id` + `payment_source`) on `ManualTopupRequest`; look up before inserting (mirror `StripeWebhookService._check_idempotency`); rely on the DB unique constraint as backstop and catch `IntegrityError` → return the existing entry.
2. Have the frontend pass `stripe:{session.id}` as the key (fixes F-01 end-to-end).
3. Add an amount ceiling and a per-admin audit trail while here.

---

### B-02 — HIGH — Stripe crediting is broken/dangerous end-to-end (three interacting defects)

**a) The frontend's credit call cannot authenticate — dead path.**
`Morpheus-Marketplace-APP/src/app/api/webhooks/stripe/route.ts:18-27` sends only `Content-Type` and `X-Admin-Secret` headers — **no `Authorization: Bearer`**. This backend's endpoint requires `current_user: User = Depends(get_current_user)` (`admin.py:193`), and `get_current_user` raises 401 when no token is present (`src/dependencies.py:66-72`). Every credit attempt via this path must fail 401 → the frontend throws → Stripe retries → still 401. Payments captured, credits never applied.

**b) Metadata key mismatch — backend webhook can't resolve the user.**
The frontend checkout writes `metadata: { userId, amount, type }` (`Morpheus-Marketplace-APP/src/app/api/stripe/create-checkout/route.ts:56-60`). This backend's webhook reads `metadata.get("user_id")` (`src/services/stripe_webhook_service.py:147`), falls back to `client_reference_id` (unset by the frontend checkout), fails `int()` on a Cognito-sub-shaped value, and returns "User not found" → HTTP 500 → Stripe retries → never credits.

**c) Two parallel webhook implementations with disjoint idempotency namespaces.**
The frontend (`/api/webhooks/stripe`, credits via non-idempotent B-01 path) and this backend (`/api/v1/webhooks/stripe`, credits via `stripe:{event_id}:{type}` + transaction-ID dedupe) both handle `checkout.session.completed`. If Stripe is configured with both endpoints, one payment credits **twice** through two non-overlapping dedupe schemes.

**[INFERENCE]** Which defect is live depends on the Stripe dashboard endpoint configuration, which is not in either repo. Statically: at most one path can be working, and the working one cannot be the frontend's. Confirm the configured endpoint; if it is the frontend's, every Stripe top-up since that configuration has failed to credit (check Stripe's webhook attempt log for 401s).

**Fix:**
1. Pick **one** canonical webhook — this backend's (it is idempotent and credits `amount_total`, not client metadata). Delete the frontend webhook + its `creditUserAccount`.
2. Fix the metadata contract: frontend checkout should set `metadata.user_id` (backend DB ID) or `client_reference_id` (Cognito sub); better, derive user server-side from the Cognito token (frontend F-02 fix) instead of trusting the body.
3. Add alerting on webhook 4xx/5xx streaks — this failure mode was silent.

---

### B-03 — MEDIUM — `GET /coinbase/payment-links/{id}` is not token-scoped (backend half of APP F-06)

**Evidence:** `src/api/v1/billing/coinbase.py:78-105` — requires `get_current_user` but fetches **any** payment link by ID via `coinbase_payment_link_service.get_payment_link(payment_link_id)` with no ownership check. The response includes `metadata`, which `create_payment_link` populates with the creator's `cognito_user_id` (`coinbase.py:36-37`).

**Impact:** Any authenticated user can enumerate/probe payment-link IDs (24-char hex — hard to guess, but they leak via logs, analytics, referrer headers) and read other users' link status, amounts, and Cognito subs. This is the "token-scoped lookup" the frontend audit deferred.

**Fix:** Record `metadata.user_id` on creation (already done) and reject reads where `metadata.user_id != current_user.cognito_user_id` (404, not 403, to avoid enumeration). Apply the same scoping to the frontend proxy's GET (APP F-06).

---

### B-04 — HIGH — Finalize can capture more than the hold → negative balance

**Evidence:**
- Hold sufficiency is checked under `SELECT ... FOR UPDATE` — but only against the **estimate** (`src/services/billing_service.py:298-318`), and the estimate is directly sized by the client's `max_tokens` (`src/services/token_estimation_service.py:90-95`).
- `finalize_usage` recomputes cost from **actual** provider token counts and applies `paid_posted_delta=-paid_charge` with no cap against the hold and no re-check of balance (`billing_service.py:429-478`).
- `update_balance` is unchecked SQL arithmetic; no DB constraint prevents negative `paid_posted_balance` (`src/crud/credits.py:196-199`).

**Impact:** Send a request with a tiny `max_tokens` (small hold passes sufficiency) that actually generates far more output (or costs more per token than estimated) → capture exceeds hold → balance goes negative → user got inference they couldn't pay for. Repeatable.

**Fix:** At finalize, re-lock the balance row (`for_update=True`) and either cap capture at the hold (plus explicit overage policy) or reject/flag captures that would drive balance below zero. Add a DB `CHECK (paid_posted_balance >= 0)` or floor as backstop. Consider a minimum output-token floor in the estimator so `max_tokens=1` can't shrink holds.

---

### B-05 — HIGH — Stale-hold reaper voids in-flight holds; finalize ignores `voided`

**Evidence:**
- `HOLD_MAX_PENDING_SECONDS` defaults to 3600 (`src/core/config.py:316`); a background loop calls `void_stale_holds` (`src/main.py:284-320`, `src/crud/credits.py:395-426`).
- `finalize_usage` short-circuits only on `posted`+`usage_charge`; it does **not** guard `status == voided` (`billing_service.py:411-424`). After a stale void, the hold amounts read as 0 yet finalize still posts a charge (`billing_service.py:442-478`).

**Impact:** A long-running stream (> 1h pending hold, or a shorter window if the setting is lowered) races the reaper: (a) stream completes with 0 recorded tokens after the void → **free inference**; (b) stream completes with usage → charge posted with no hold backing (compounds B-04). Long generations, retries, and failover stalls widen the window.

**Fix:** Finalize must reject/complain loudly on `voided` holds (and re-create a hold or charge with an explicit audit entry). The reaper should skip holds with an active-request marker (heartbeat/lease) rather than pure age.

---

### B-06 — MEDIUM — Revenue-loss paths around finalize

1. **Disconnect voids the hold despite partial delivery** — `chat_streaming.py:383-391,248-264`: `CancelledError` → not `stream_completed_successfully` → cleanup voids instead of finalizing accumulated tokens. Users can repeatedly disconnect mid-stream after consuming most of a response. (Revenue loss, not overcharge.)
2. **Finalize errors leave holds pending** — `chat/index.py:667-678` and `embeddings/index.py:548-558` swallow finalize exceptions after returning 200; the hold is neither captured nor voided until B-05's reaper voids it → free usage.
3. **Missing usage finalizes at $0** — `chat_streaming.py:507-512,541-542`: if the provider stream ends without a `usage` chunk, success is still marked and finalize runs with 0 tokens → $0 charge, hold released.

**Fix:** Finalize partial accumulation on disconnect when `tokens_total > 0`; void the hold when post-200 finalize fails; treat missing usage on an otherwise successful stream as an error path (re-estimate + capture), not $0.

---

### B-07 — MEDIUM — `create_refund` trusts caller amount; idempotency keyed by `reason`

**Evidence:** `billing_service.py:602-630` — `idempotency_key = f"refund:{request.request_id}:{request.reason}"` and `refund_amount = abs(request.amount)` with no cap against the original charge; the original-entry lookup has no `entry_type` filter (`crud/credits.py:496-510`). Same charge refundable twice with different `reason` values. **No HTTP caller exists today** — service-layer only — so this is exposure for any future endpoint, not a live exploit.

**Fix:** Cap refunds at `original_charge − Σ(prior refunds)`; key idempotency on `request_id` alone (or track refund linkage via `related_entry_id`); require `entry_type == usage_charge` on the original.

---

## 4. Detailed Findings — Authentication & Authorization

### B-08 — CRITICAL — Local-testing mode bypasses all auth with no environment guard

**Evidence:** `src/core/local_testing.py:17-22` (raw `os.getenv`, requires both `LOCAL_TESTING_MODE=true` and `BYPASS_COGNITO_AUTH=true`, defaults false). When active, `get_current_user`, `get_api_key_auth`, and `get_user_jwt_or_api_key` all return a shared test user without credentials (`src/dependencies.py:52-57, 277-299, 629-631`). Not wired through `Settings`; the only production guard is a startup log warning (`src/main.py:358-359`). `env.local.example:6-7` sets both flags `true`.

**Impact:** If both vars ever reach a production environment (misconfigured deploy, copied env file, task-definition drift), **every endpoint becomes anonymously accessible as one shared user** — including billing admin endpoints when combined with the admin secret. This is a config slip away from total compromise.

**Fix:** Hard-fail at startup when bypass flags are set and `ENVIRONMENT` is not in `{local, development, test}`. Wire the flags through `Settings` so they appear in config audits.

### B-09 — HIGH — Legacy API keys authenticate on prefix only

**Evidence:** `src/dependencies.py:355-370` (and DB path `:458-471`) — when `encrypted_key is None`, authentication succeeds after lookup by the 9-char prefix (`sk-xxxxxx`) with **no hash verification**. Modern keys verify correctly via SHA-256.

**Impact:** For any legacy key row, the prefix — which is displayed in UIs and appears in logs — **is** the credential. Anyone who learns a legacy prefix (dashboard screenshot, log line, support ticket) has full API access as that user.

**Fix:** Migrate: force-rotate all rows where `encrypted_key IS NULL`, then reject such rows at auth time. Until then, alert on legacy-key authentications.

### B-10 — MEDIUM — JWT path never checks `user.is_active`

**Evidence:** `src/dependencies.py:155-203` — `get_current_user` returns the (possibly cached) user with no `is_active` check; the API-key path does check (`:497-505`). Deactivating a user does not revoke access for holders of valid Cognito JWTs (≤ 60 min access-token TTL, longer via refresh + cached user at 600 s TTL).

**Fix:** Check `is_active` in `get_current_user`; invalidate the `user:{sub}` cache entry on deactivation.

### B-11 — MEDIUM — Public `/exchange-token` mints and returns tokens; auth code in GET query

**Evidence:** `src/main.py:1440-1489` — unauthenticated GET taking `code` as a **query parameter** (→ access logs, browser history, referrer leakage), `state` accepted but never validated, and returns `access_token`/`id_token` in the JSON body. `include_in_schema=False` but the route is live. Mitigating: `redirect_uri` is pinned to `/docs/oauth2-redirect`, so a stolen code for another redirect URI won't exchange — this is a Swagger convenience tool, but it normalizes token-in-URL and token-in-body patterns and accepts codes from any caller.

**Fix:** Gate behind a non-production flag or delete; never accept credentials in query strings; validate `state` if kept.

---

## 5. Detailed Findings — Infrastructure & Configuration

### B-12 — CRITICAL — `ENCRYPTION_SECRET_KEY` defaults to a known string

**Evidence:** `src/core/config.py:158` — `ENCRYPTION_SECRET_KEY: str = Field(default=os.getenv("ENCRYPTION_SECRET_KEY", "encryption_secret_change_me"))`. This key encrypts stored user API keys (`src/core/encryption.py`).

**Impact:** Any deployment missing the env var encrypts all stored API keys with a **publicly known key from the source repo**. DB read access (backup leak, SQL injection elsewhere, insider) = decryption of every stored key. Silent — nothing warns at startup.

**Fix:** No default; fail startup when unset outside local/test. Rotate if any environment has ever run with the default.

### B-13 — HIGH — CORS allows any HTTPS origin, with credentials

**Evidence:** `src/main.py:82` — `allow_direct_access=True` ("Enable for ALB cookie stickiness from any client"); `src/core/cors_middleware.py:188-196` — step 3 returns `True` for **any** `https://` origin; the middleware then reflects the origin and emits `Access-Control-Allow-Credentials: true`.

**Impact:** Any malicious HTTPS site can make credentialed cross-origin requests to this API from a victim's browser. **Tempering factor [verified]:** API auth is Bearer-token (not ambient cookies), so a malicious site cannot authenticate *as* the victim without already holding a token — today's practical impact is low. But the moment any cookie-based auth exists (Swagger session, future BFF cookie per frontend F-07's recommended fix), this becomes an immediate full-account CSRF/read primitive.

**Fix:** Disable `allow_direct_access` outside non-prod, or scope it to an explicit origin allowlist. Solve ALB stickiness without opening origin policy.

### B-14 — HIGH — Full chat request bodies logged at INFO

**Evidence:** `src/api/v1/chat/chat_utils.py:93-101` — `logger.info("Tool calling request detected", ..., request_body=json_body)`, reached unconditionally for any request with tools/tool messages (`src/api/v1/chat/index.py:206`). Additionally `src/dependencies.py:75-76` logs `token_preview=token.credentials[:20]` at DEBUG, and `env.example:223` suggests `LOG_LEVEL_API=DEBUG`.

**Impact:** User prompts, tool arguments (which routinely contain credentials, file contents, PII) are written to production logs at default level. Anyone with log access (CloudWatch retention, support tooling, log-shipping vendors) reads user data. Likely GDPR/CCPA-relevant.

**Fix:** Drop `request_body` from log fields (log counts/hashes); gate previews behind an explicit PII-logging flag; scrub token previews.

### B-15 — HIGH — Rate limiting fails open when Redis is unavailable

**Evidence:** `src/services/rate_limiting/redis_limiter.py:11-18, 282-284, 313-315`; `src/services/rate_limiting/rate_limit_service.py:241-245` — on Redis outage/circuit-breaker open, requests are allowed through with no RPM/TPM enforcement.

**Impact:** A Redis failure (or induced failure) removes all throttling: cost amplification via expensive models, and easier credential-stuffing/enumeration. [Verified mitigating:] rate limits are keyed by API-key prefix, not spoofable client IP.

**Fix:** Configurable fail-closed (shed load or degrade to a small in-process limiter) for billing-relevant routes; alert on limiter degradation.

### B-16 — HIGH — Hardcoded production Cognito defaults

**Evidence:** `src/core/config.py:172-176` — real-looking production values as defaults: `COGNITO_USER_POOL_ID` default `us-east-2_tqCTHoSST`, `COGNITO_CLIENT_ID` default `7faqqo5lcj3175epjqs2upvmmu`, derived `COGNITO_JWKS_URL`.

**Impact:** A misconfigured environment silently authenticates against the **production** user pool (dev/staging tokens accepted as prod identities or vice versa). Also embeds production infrastructure identifiers in source.

**Fix:** No defaults for pool/client IDs; fail startup when unset outside local/test.

### B-17 — MEDIUM — Signup-bonus IP guard trusts spoofable `X-Forwarded-For`

**Evidence:** `src/api/v1/billing/index.py:56-60` takes the first `X-Forwarded-For` hop verbatim; `src/crud/credits.py:650-656` enforces one bonus per IP per window on that value. Combined with B-13's direct-access posture, a client reaching the app directly supplies the header itself.

**Impact:** Sybil signup-bonus farming: spoof a fresh IP per account → `$SIGNUP_BONUS_AMOUNT` (default $1) per account, unlimited.

**Fix:** Resolve client IP from a trusted-proxy list (rightmost untrusted hop), or enforce at the edge/WAF.

### B-18 — MEDIUM — Internal error strings returned in 500s

**Evidence:** `src/dependencies.py:225-236, 337-339` ("Authentication error: {str(e)}", DB/Cognito specifics); all billing endpoints (`src/api/v1/billing/index.py:71-73, 123-125, 220-222, 293-295, 372-374, 453-455`); admin endpoints (`admin.py:250-260` et al.) — `detail=f"...: {str(e)}"`.

**Fix:** Generic client messages; details server-side only. (Note: `src/utils/error_sanitizer.py` already strips URLs/tokens/IPs — route these handlers through it.)

### B-19 — MEDIUM — AES-CBC without authenticated encryption

**Evidence:** `src/core/encryption.py:76-80` — AES-CBC + PKCS7, random IV (good) but no GCM/HMAC → malleable ciphertexts, no integrity detection on stored API keys.

**Fix:** Migrate to AES-GCM (or Fernet); version the ciphertext format for rotation.

### B-20 — MEDIUM — Operational hygiene cluster

- DB schema version check commented out at startup (`src/main.py:367-378`) — app boots against out-of-sync schema.
- CI manually rewrites `alembic_version` to hardcoded strings (`.github/workflows/build.yml:447-453`).
- CI tests allowed to fail (`build.yml:211-212`); unpinned actions (`snok/install-poetry@latest`, `actions/cache@v3`, `anzz1/action-create-release@v1`).
- Public info endpoints disclose internals: `/health` (container UUID, Redis stats, model URLs — `main.py:670-774`), `/cors-check` (full CORS config — `main.py:840-923`).
- No TrustedHostMiddleware, no app-layer request-size limit; HTTPS redirect trusts `X-Forwarded-Proto` without a trusted-proxy list (`main.py:153-162`).
- `docker-compose.local.yml` weak DB password + `PROXY_ROUTER admin/admin`; `scripts/awsonboard:102-106` writes a known password; `scripts/deploy-with-testing.sh:25` defaults to the production AWS profile.

### B-21 — LOW — JWT/API-key hardening gaps

- No `token_use` claim validation (`dependencies.py:126-141`; audience check commented out and replaced with manual `client_id` check — correct for Cognito access tokens, but `token_use == "access"` should be asserted).
- API-key hash comparison uses `==` instead of `hmac.compare_digest` (`src/core/security.py:46-47`) — low practical risk over SHA-256 of 256-bit keys.

### B-22 — LOW — `python-jose` unmaintained

**Evidence:** `pyproject.toml:19` — `python-jose ^3.3.0` (CVE-2024-33663 algorithm confusion, CVE-2024-33664 JWE DoS). Usage here is `jwt.decode` with `algorithms=['RS256']` against Cognito JWKS, so exploitability is minimal, but the library is unmaintained.

**Fix:** Migrate to `PyJWT` or `authlib`.

### B-23 — INFO — Audio unbilled; delegated SSRF surface

- `src/api/v1/audio/index.py` — STT/TTS proxy with no hold/finalize/void: unlimited free usage on audio paths.
- `audio/index.py:45` accepts an arbitrary `s3_presigned_url` form field forwarded to the proxy-router (`src/services/proxy_router_service.py:1005-1006`) without host/scheme validation — SSRF risk lives in the proxy-router (out of scope here), validate before forwarding regardless.

---

## 6. Framework Checklist Results

### OWASP API Security Top 10 (2023)

| Category | Status | Notes |
|---|---|---|
| API1 BOLA | ⚠️ | B-03 (payment links); chat history/wallet/keys/billing verified clean |
| API2 Broken Authentication | ❌ | B-08, B-09, B-10, B-11 |
| API3 Object Property Level | ✅ | Responses are typed schemas |
| API4 Resource Consumption | ⚠️ | B-15 (fail-open limits), B-23 (unbilled audio), no body-size cap |
| API5 Function Level Authz | ⚠️ | Admin endpoints gated by secret+JWT; B-08 bypass switch undermines all of it |
| API6 Sensitive Business Flows | ❌ | B-01, B-02, B-04, B-05, B-06, B-07 (money movement) |
| API7 SSRF | ⚠️ | B-23 (`s3_presigned_url` forwarded); server-side fetches otherwise constant/env-configured |
| API8 Misconfiguration | ❌ | B-12, B-13, B-16, B-20 |
| API9 Inventory Management | ⚠️ | Public debug endpoints (B-20), `include_in_schema=False` routes still live (B-11) |
| API10 Unsafe API Consumption | ✅ | Stripe/Coinbase/CDP responses handled defensively; Decimal money math |

### OWASP Top 10 (2021) — deltas vs frontend report

| Category | Status | Notes |
|---|---|---|
| A01 Broken Access Control | ⚠️ | B-03, B-10 |
| A02 Cryptographic Failures | ❌ | B-12, B-19, B-21 |
| A03 Injection | ✅ | No raw/f-string SQL found anywhere in `src/`; ORM + bound params |
| A04 Insecure Design | ❌ | B-01, B-02, B-04, B-05, B-08 |
| A05 Security Misconfiguration | ❌ | B-13, B-16, B-20 |
| A06 Vulnerable Components | ⚠️ | B-22; full SCA not run (`pip-audit` unavailable in audit env) — run in CI |
| A07 AuthN Failures | ❌ | B-08, B-09, B-11 |
| A08 Integrity | ⚠️ | B-20 (CI: unpinned actions, alembic rewrite, non-blocking tests) |
| A09 Logging & Monitoring | ❌ | B-14 (over-logging), no webhook-failure alerting (B-02 silent failure mode) |
| A10 SSRF | ⚠️ | B-23 |

---

## 7. Prioritized Remediation Plan

**P0 — this week (money + auth):**
1. B-01: idempotency key on `/credits/adjust` (pairs with frontend F-01 fix — pass `stripe:{session.id}`).
2. B-02: pick the backend webhook as canonical; delete the frontend webhook path; fix the `user_id`/`client_reference_id` metadata contract; alert on webhook error streaks. Verify against Stripe's attempt log whether past top-ups 401'd.
3. B-08: startup hard-fail on auth-bypass flags outside local/test.
4. B-12: remove the encryption-key default; rotate if the default was ever live.
5. B-04: cap capture at hold / re-check balance under lock; DB floor at 0.

**P1 — this month:**
6. B-05: finalize rejects voided holds; reaper respects in-flight leases.
7. B-09: force-rotate legacy API keys; reject prefix-only rows.
8. B-13: disable `allow_direct_access` in prod or scope to an allowlist.
9. B-14: remove `request_body` from logs; scrub token previews.
10. B-03: token-scope payment-link reads (with frontend F-06 fix).
11. B-10: `is_active` check on JWT path.
12. B-15: fail-closed rate-limit mode for billing routes.

**P2 — this quarter:**
13. B-06: partial-finalize on disconnect; no $0 finalizes on missing usage; void on post-200 finalize failure.
14. B-07: refund caps + idempotency by `request_id`.
15. B-11: delete or gate `/exchange-token`.
16. B-16/B-17/B-18/B-19/B-20/B-21/B-22/B-23: config fail-closed sweep, trusted-proxy IP resolution, error sanitization, AES-GCM migration, CI hardening, `token_use` validation, `PyJWT` migration, audio billing, presigned-URL validation.

**Verification per fix:** replay a signed Stripe event twice → one credit (B-01/B-02); credit call without Bearer → 401 documented as expected for the deleted path; `max_tokens=1` with large actual output → capture capped (B-04); hold older than `HOLD_MAX_PENDING_SECONDS` mid-stream → finalize rejects, no free usage (B-05); both bypass env vars set with `ENVIRONMENT=production` → startup abort (B-08); legacy-prefix key → 401 (B-09); `curl -H "Origin: https://evil.example" -H "Cookie: ..."` preflight → no `Allow-Credentials` (B-13); tool-calling chat request → no body in logs (B-14); Redis down → 429/503, not open access (B-15).

---

## 8. Limitations

- Static review only; no dynamic/authenticated testing, no fuzzing.
- **Runtime topology unknown:** which Stripe webhook endpoint is configured (B-02), actual env values in each deployment (B-08/B-12/B-13/B-16 severity assumes defaults can reach prod), and proxy-router behavior for B-23's forwarded URLs are all deployment-side and need operational confirmation.
- SCA incomplete: `pip-audit` was unavailable in the audit environment; only `python-jose` was assessed manually. Run `pip-audit`/`safety` in CI.
- The proxy-router, Cognito pool configuration, WAF/ALB rules, and the Hermes job infrastructure are out of scope.
- Subagent-assisted sweep (3 slices); every Critical/High claim in §3–§5 was independently re-verified against source before inclusion.
