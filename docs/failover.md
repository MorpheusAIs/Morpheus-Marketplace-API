# Gateway-Owned Session Recovery

Two separate mechanisms, both bounded to exactly one retry per request:

## 1. Provider failover (different provider)

Applies only when the provider becomes **unavailable during a session**
(connection refused, dial/read timeouts, provider death, proxy 5xx,
gateway↔proxy transport failures) **and the model has an alternate bid**.

1. **Alternate-bid check first.** The model must have **more than one** rated
   bid (`GET blockchain/models/{id}/bids/rated`). If it has only one bid there
   is nowhere to fail over to, so the session is **left OPEN** (NOT invalidated
   or closed early): it rides to its natural on-chain expiry, the user's MOR is
   **not** locked, and the original error is surfaced with no retry. This is
   the pre-failover behavior and avoids the open→fail→early-close→reopen churn
   (the bulk of it, since the dead single-bid legacy models drive ~98% of
   failover attempts).
2. With a sibling bid present, the `routed_sessions` row is marked `FAILED`
   (it will never be routed to again) and closed on the proxy-router in the
   background (best-effort; close works against a dead provider via the
   self-signed user report, and the proxy-router's expiry handler is the
   backstop).
3. A fresh session is opened by model: the proxy-router tries bids
   best-first with a per-bid provider handshake, so the dead provider is
   skipped automatically and the session lands on the surviving provider.
4. The prompt is retried once. If the retry fails, a clean error (real HTTP
   status) is returned.

## 2. Expired-session renewal (same model, healthy provider)

Applies when the proxy reports `session expired` (the session TTL'd out;
the provider is fine). This is NOT failover:

1. The old `routed_sessions` row is marked `EXPIRED` (its DB expires_at may
   still be in the future) and closed in the background.
2. A new session is routed for the same model — no alternate-bid
   requirement; works on single-bid models. Bid ranking is deterministic,
   so this typically lands on the same provider.
3. The prompt is retried once.

No recovery for: `session not found`, user/4xx errors, AI-engine errors,
TEE attestation failures, client-cancelled requests.

Kill switch: `CHAT_FAILOVER_ENABLED=false` (env) disables the failover
mechanism (renewal is independent of the flag).

## Streaming

Recovery happens only **before the first provider byte reaches the client**.
Once tokens are flowing, a failure terminates the stream with an SSE error
chunk (`data: {"error": ...}`) and the billing hold is voided. We deliberately
do not restart half-delivered answers.

Known limitation: if a provider dies mid-stream after sending data, the
proxy-router currently ends the stream as if successful (HTTP 200, truncated,
no error marker); the gateway cannot distinguish this from normal completion.

## Billing

Exactly one usage hold per request (created before session resolution);
recovery retries run inside the request handler against the same hold.
Finalize happens only for a 200 outcome; every failure path voids. Session
accounting: `index.py` (non-streaming) / `_stream_cleanup` (streaming)
releases the original session, the retry path releases the new session, and
`FAILED`/`EXPIRED` sessions are excluded from routing.

## Why not proxy-router failover=true

The c-node's failover closes/reopens sessions underneath the client and the
new sessionID diverges from `routed_sessions` (it only signals the change via
SSE control chunks, which also corrupt non-streaming JSON responses). The flag
is additionally non-functional in the current proxy-router build. The gateway
therefore always opens sessions with `failover: false`.

## Manual acceptance test

Setup: a model with bids from two providers (provider1, provider2); local
proxy-router as consumer node; gateway pointed at it via `PROXY_ROUTER_URL`.

Provider failover:
1. Send a prompt → verify it succeeds; note `session_id` S1 in
   `routed_sessions` (state OPEN) and which provider serves it.
2. Kill provider1's node (`docker kill` / stop the process).
3. Send the next prompt with the same API key:
   - it must succeed transparently (one retry, served by provider2),
   - `routed_sessions` shows S1 `state=FAILED` with `error_reason` starting
     with "recovery:", and a new row S2 `state=OPEN`,
   - logs show `failover_triggered` → `failover_new_session` →
     `recovery_retry_success` (or `stream_failover_retry_start` for
     streaming),
   - billing ledger has exactly one entry for the request (no double charge,
     status posted, tokens from the successful attempt).
4. Kill provider2 as well, send a prompt → clean 5xx error (`retry_failed` or
   original error), hold voided, no infinite retry.

Expired-session renewal:
5. Use a model with a SINGLE bid and a short session duration
   (`SESSION_DEFAULT_DURATION_SECONDS`); open a session, wait past its
   on-chain expiry, send a prompt:
   - it must succeed transparently (renewal retry),
   - old row `state=EXPIRED`, new row OPEN,
   - logs show `session_expired_detected` → `session_retry_start` →
     `recovery_retry_success`.

Kill switch:
6. Set `CHAT_FAILOVER_ENABLED=false`, kill provider1, send a prompt → error
   surfaces immediately with no failover retry (expired-session renewal
   still works).
