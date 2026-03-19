# Coinbase Commerce to Business Migration - Backend Review Checklist

**Migration Deadline: March 31, 2026**
**Reference**: [Coinbase Transition Guide](https://help.coinbase.com/en/transitioning-from-coinbase-commerce-to-coinbase-business)

---

## 1. Coinbase Dashboard / Account Setup

- [ ] **Create Coinbase Business account** (or convert existing Commerce account)
  - [Getting Started](https://docs.cdp.coinbase.com/coinbase-business/introduction/get-started)
- [ ] **Complete KYB (Know Your Business) verification** if not already done
- [ ] **Generate CDP API Key** in the [CDP Portal](https://portal.cdp.coinbase.com/access/api)
  - Select **ES256** algorithm
  - Enable **View** scope (covers Payment Link CRUD)
  - Download the private key PEM file — it's shown only once
  - Note the key name format: `organizations/{org_id}/apiKeys/{key_id}`

---

## 2. Environment Variables to Configure

| Variable | Description | Where |
|---|---|---|
| `CDP_API_KEY_NAME` | Key name from CDP portal (`organizations/{org_id}/apiKeys/{key_id}`) | All environments |
| `CDP_API_KEY_PRIVATE_KEY` | EC private key PEM (newlines as `\n`) | All environments (secrets manager) |
| `COINBASE_PAYMENT_LINK_WEBHOOK_SECRET` | From webhook subscription metadata | All environments |

### Variables to Remove

| Variable | Reason |
|---|---|
| `COINBASE_COMMERCE_WEBHOOK_SECRET` | Legacy Commerce API — no longer used |

---

## 3. Webhook Configuration

- [ ] **Register webhook endpoint** in Coinbase Business dashboard
  - URL: `https://<api-domain>/api/v1/webhooks/coinbase`
  - Content-Type: `application/json`
- [ ] **Subscribe to events**:
  - `payment_link.payment.success`
  - `payment_link.payment.failed`
  - `payment_link.payment.expired`
- [ ] **Save the webhook secret** from the subscription metadata response
  - Set as `COINBASE_PAYMENT_LINK_WEBHOOK_SECRET` env var
- [ ] **Test webhook delivery** using Coinbase's test tools or [Postman collection](https://docs.cdp.coinbase.com/coinbase-business/payment-link-apis/postman-files)

### Webhook Signature Format (new)
- Header: `X-Hook0-Signature`
- Format: `t=<timestamp>,h=<header_names>,v1=<hmac_sha256>`
- Replay protection: Rejects events older than 5 minutes

### Old Format (removed)
- Header: `X-CC-Webhook-Signature` — no longer accepted by our endpoint

---

## 4. API Authentication Changes

### Old (Commerce)
```
X-CC-Api-Key: <api_key>
X-CC-Version: 2018-03-22
```

### New (CDP / Business)
```
Authorization: Bearer <ES256_JWT>
Content-Type: application/json
```

The JWT is generated **per request** with:
- `sub`: CDP key name
- `iss`: `"cdp"`
- `uri`: `"{METHOD} api.coinbase.com{PATH}"`
- `exp`: current time + 120 seconds
- `nonce`: random hex

Implementation: `src/services/coinbase_auth.py`

---

## 5. Payment Link API Endpoints

Base URL: `https://api.coinbase.com`

| Operation | Method | Path |
|---|---|---|
| Create | `POST` | `/api/v1/payment-links` |
| List | `GET` | `/api/v1/payment-links` |
| Get | `GET` | `/api/v1/payment-links/{id}` |
| Deactivate | `POST` | `/api/v1/payment-links/{id}/deactivate` |

### Key Differences from Commerce Charges

| Aspect | Commerce (old) | Payment Link (new) |
|---|---|---|
| ID format | UUID | 24-char hex |
| URL field | `hosted_url` | `url` |
| Amount | `pricing.local.amount` | `amount` (flat) |
| Currency | `pricing.local.currency` | `currency` (flat) |
| Status | `timeline` array | Single `status` field |
| Statuses | NEW, SIGNED, PENDING, COMPLETED | ACTIVE, COMPLETED, EXPIRED, DEACTIVATED |
| Currencies | BTC, ETH, USDC, DAI, USD | **USDC only** |
| Network | Multiple | **Base only** |
| Idempotency | Not required | `X-Idempotency-Key` header |

---

## 6. Currency Limitation — Important

The Payment Link API currently **only supports USDC on Base network**. If users were previously paying with BTC, ETH, or other currencies via Commerce, they will need to use USDC going forward.

Verify:
- [ ] Frontend payment UI reflects USDC-only
- [ ] Any documentation/help text referencing multi-currency is updated
- [ ] Pricing is displayed in USDC (1:1 with USD)

---

## 7. Database / Data Migration

No schema changes required — the `credits_ledger` table already supports both formats via the `payment_metadata` JSONB column.

### Verify
- [ ] Existing `payment_metadata` entries with `"type": "charge"` remain queryable
- [ ] New entries will have `"type": "payment_link"`
- [ ] `external_transaction_id` now stores 24-char hex IDs (was UUID charge codes)
- [ ] No migration script needed for existing data

---

## 8. Testing Checklist

### Pre-deployment
- [ ] Verify JWT signing works with test CDP key
- [ ] Create a test payment link via admin endpoint
- [ ] Verify webhook signature verification with test payload
- [ ] Test idempotency (same webhook delivered twice)
- [ ] Test expired/failed webhook handling

### Post-deployment
- [ ] Create a real payment link and complete payment
- [ ] Verify credits appear in user's balance
- [ ] Verify payment metadata is stored correctly
- [ ] Monitor logs for `coinbase_pl_webhook_*` events
- [ ] Verify deactivation works

### Postman Collection
Coinbase provides a [Postman collection](https://docs.cdp.coinbase.com/coinbase-business/payment-link-apis/postman-files) for testing all Payment Link API endpoints.

---

## 9. Rollback Plan

If issues are discovered after deployment:

1. The webhook endpoint only accepts `X-Hook0-Signature` — if you need to revert, restore the legacy `verify_legacy_commerce_signature` function from git history
2. CDP API key credentials can coexist with Commerce API keys during transition
3. The `COINBASE_COMMERCE_WEBHOOK_SECRET` config was removed from code but the env var can remain set harmlessly

---

## 10. IP Allowlisting

- [ ] If using CDP API key IP allowlisting, ensure all API server IPs are added
- [ ] If behind a load balancer, verify the outbound IP (NAT gateway) is allowlisted

---

## 11. Monitoring & Alerting

Ensure alerts are configured for these log event types:
- `coinbase_pl_webhook_not_configured` — Secret missing (critical)
- `coinbase_pl_webhook_invalid_signature` — Signature mismatch (security)
- `coinbase_pl_webhook_replay` — Replay attack attempt (security)
- `admin_payment_link_error` — API call failures (operational)

---

## 12. Documentation References

- [Migration Overview](https://docs.cdp.coinbase.com/coinbase-business/payment-link-apis/migrate/overview)
- [API & Schema Mapping](https://docs.cdp.coinbase.com/coinbase-business/payment-link-apis/migrate/api-schema-mapping)
- [Payment Link API Reference](https://docs.cdp.coinbase.com/api-reference/business-api/rest-api/payment-links/introduction)
- [CDP API Key Auth](https://docs.cdp.coinbase.com/coinbase-business/authentication-authorization/api-key-authentication)
- [Webhook Docs](https://docs.cdp.coinbase.com/coinbase-business/payment-link-apis/webhooks)
- [Migration FAQ](https://docs.cdp.coinbase.com/coinbase-business/payment-link-apis/migrate/faq)
