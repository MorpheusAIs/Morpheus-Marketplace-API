# Coinbase Commerce to Business Migration - Backend Review Checklist

**Migration Deadline: March 31, 2026**
**Reference**: [Coinbase Transition Guide](https://help.coinbase.com/en/transitioning-from-coinbase-commerce-to-coinbase-business)

---

## 1. Coinbase Dashboard / Account Setup

- [ ] **Create Coinbase Business account** (or convert existing Commerce account)
  - [Getting Started](https://docs.cdp.coinbase.com/coinbase-business/introduction/get-started)
- [ ] **Complete KYB (Know Your Business) verification** if not already done
- [ ] **Generate CDP Secret API Key** in the [CDP Portal](https://portal.cdp.coinbase.com/projects/api-keys)
  - Go to the **Secret API Keys** tab and click **Create API key**
  - Signature algorithm: Ed25519 (recommended) or ECDSA
  - Save the **Key ID** (UUID) → `CDP_API_KEY_ID`
  - Save the **Key Secret** (base64 string) → `CDP_API_KEY_SECRET`
  - These are shown only once

---

## 2. Environment Variables to Configure

| Variable | Description | Where |
|---|---|---|
| `CDP_API_KEY_ID` | Secret API Key ID (UUID) from [CDP Portal](https://portal.cdp.coinbase.com/projects/api-keys) | All environments |
| `CDP_API_KEY_SECRET` | Secret API Key secret (base64) from CDP Portal | All environments (secrets manager) |
| `CDP_SANDBOX` | `true` for sandbox (no real transactions), `false` for production | Per environment |
| `COINBASE_PAYMENT_LINK_WEBHOOK_SECRET` | From webhook subscription metadata | All environments |

### Variables to Keep (Legacy - Transition Period)

| Variable | Reason |
|---|---|
| `COINBASE_COMMERCE_WEBHOOK_SECRET` | Legacy Commerce API — **kept during transition period** for backward compatibility |

> **Note:** Legacy Commerce variables will be removed after migration is confirmed complete and all in-flight Commerce charges have settled.

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

### Old Format (still supported during transition)
- Header: `X-CC-Webhook-Signature` — legacy Commerce format
- The webhook endpoint auto-detects the format based on which signature header is present
- Both formats are supported simultaneously via `_detect_webhook_format()`

---

## 4. API Authentication Changes

### Old (Commerce)
```
X-CC-Api-Key: <api_key>
X-CC-Version: 2018-03-22
```

### New (CDP / Business)
```
Authorization: Bearer <signed_JWT>
Content-Type: application/json
```

JWT generation is handled by the `cdp-sdk` Python package using:
- `CDP_API_KEY_ID` (UUID) and `CDP_API_KEY_SECRET` (base64)
- Supports both Ed25519 and ECDSA key types (SDK auto-detects)
- See: https://docs.cdp.coinbase.com/api-reference/v2/authentication

Implementation: `src/services/coinbase_auth.py`

---

## 5. Payment Link API Endpoints

### Coinbase Business API (upstream)

Base URL: `https://business.coinbase.com`

| Operation | Method | Production Path | Sandbox Path |
|---|---|---|---|
| Create | `POST` | `/api/v1/payment-links` | `/sandbox/api/v1/payment-links` |
| List | `GET` | `/api/v1/payment-links` | `/sandbox/api/v1/payment-links` |
| Get | `GET` | `/api/v1/payment-links/{id}` | `/sandbox/api/v1/payment-links/{id}` |
| Deactivate | `POST` | `/api/v1/payment-links/{id}/deactivate` | `/sandbox/api/v1/payment-links/{id}/deactivate` |

Controlled by `CDP_SANDBOX` env var. See [Sandbox docs](https://docs.cdp.coinbase.com/coinbase-business/payment-link-apis/sandbox).

### Our API Endpoints

**User-facing** (under `/api/v1/billing/coinbase/`):

| Operation | Method | Path | Auth |
|---|---|---|---|
| Create Payment Link | `POST` | `/api/v1/billing/coinbase/payment-links` | User (Cognito JWT) |
| Get Payment Link | `GET` | `/api/v1/billing/coinbase/payment-links/{id}` | User (Cognito JWT) |

Implementation: `src/api/v1/billing/coinbase.py`

**Admin-only** (under `/api/v1/billing/`, requires `X-Admin-Secret`):

| Operation | Method | Path | Auth |
|---|---|---|---|
| List Payment Links | `GET` | `/api/v1/billing/payment-links` | Admin (X-Admin-Secret) |
| Deactivate Payment Link | `POST` | `/api/v1/billing/payment-links/{id}/deactivate` | Admin (X-Admin-Secret) |

Implementation: `src/api/v1/billing/admin.py`

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

## 8. User Identification in Payment Flow

The `metadata` field on the payment link is used to pass the user identifier through the Coinbase payment flow. Coinbase treats metadata as an opaque key-value store and returns it verbatim in webhook payloads.

### Flow

1. **Create** (`POST /api/v1/billing/coinbase/payment-links`):
   The authenticated user's `cognito_user_id` is **automatically injected** into `metadata.user_id` server-side. The caller cannot override this — it is set from the JWT-authenticated session.

   ```json
   // Sent to Coinbase API:
   {
     "amount": "10.00",
     "currency": "USDC",
     "metadata": {
       "user_id": "<cognito_user_id>"
     }
   }
   ```

2. **Webhook** (`POST /api/v1/webhooks/coinbase`):
   Coinbase sends the `metadata` back in the event payload. The webhook handler reads `metadata.user_id` to look up the user and credit their account.

   ```json
   // Received from Coinbase:
   {
     "id": "69163c762331ed43dc64a6ef",
     "eventType": "payment_link.payment.success",
     "amount": "10.00",
     "currency": "USDC",
     "metadata": {
       "user_id": "<cognito_user_id>"
     },
     ...
   }
   ```

3. **Credit**: The webhook service looks up the user by `cognito_user_id`, validates the amount, and creates a purchase ledger entry.

### Implementation
- Injection: `src/api/v1/billing/coinbase.py` → `metadata["user_id"] = current_user.cognito_user_id`
- Extraction: `src/services/coinbase_webhook_service.py` → `_get_user_from_metadata()`

---

## 9. Transition Architecture (Dual Webhook Support)

During the transition period, the system supports **both** Commerce and Payment Link webhooks simultaneously:

```
POST /api/v1/webhooks/coinbase
    ├── X-Hook0-Signature header present  → Payment Link handler (new)
    └── X-CC-Webhook-Signature header present → Legacy Commerce handler (deprecated)
```

### Files involved:
- `src/api/v1/webhooks/coinbase.py` — Dual-format webhook endpoint with auto-detection
- `src/services/coinbase_webhook_service.py` — Event handlers for both formats
- `src/api/v1/billing/coinbase.py` — New Payment Link CRUD endpoints (user-facing)
- `src/services/coinbase_payment_link_service.py` — Payment Link API client
- `src/services/coinbase_auth.py` — CDP JWT auth for API calls
- `src/core/config.py` — Both `COINBASE_COMMERCE_WEBHOOK_SECRET` and `COINBASE_PAYMENT_LINK_WEBHOOK_SECRET`

---

## 10. Testing Checklist

### Sandbox Testing
- [ ] Set `CDP_SANDBOX=true` and verify API calls go to `/sandbox/api/v1/payment-links`
- [ ] Create a sandbox payment link and complete payment with [testnet USDC](https://portal.cdp.coinbase.com/products/faucet)
- [ ] Register a sandbox webhook subscription (with `"sandbox": "true"` label)
- [ ] Verify sandbox webhook events are received and processed correctly

### Pre-deployment
- [ ] Verify JWT signing works with test CDP key
- [ ] Create a test payment link via `POST /api/v1/billing/coinbase/payment-links`
- [ ] Verify webhook signature verification with test payload (both formats)
- [ ] Test idempotency (same webhook delivered twice)
- [ ] Test expired/failed webhook handling
- [ ] Verify legacy Commerce webhooks still work during transition

### Post-deployment
- [ ] Create a real payment link and complete payment
- [ ] Verify credits appear in user's balance
- [ ] Verify payment metadata is stored correctly
- [ ] Monitor logs for `coinbase_pl_webhook_*` events
- [ ] Verify deactivation works

### Postman Collection
Coinbase provides a [Postman collection](https://docs.cdp.coinbase.com/coinbase-business/payment-link-apis/postman-files) for testing all Payment Link API endpoints.

---

## 11. Rollback Plan

If issues are discovered after deployment:

1. The webhook endpoint supports both `X-Hook0-Signature` and `X-CC-Webhook-Signature` — legacy Commerce continues to work without code changes
2. CDP API key credentials coexist with Commerce API keys during transition
3. Both `COINBASE_COMMERCE_WEBHOOK_SECRET` and `COINBASE_PAYMENT_LINK_WEBHOOK_SECRET` can be set simultaneously

---

## 12. Post-Migration Cleanup (after transition)

Once all Commerce charges have settled and new system is confirmed working:

- [ ] Remove `COINBASE_COMMERCE_WEBHOOK_SECRET` from config
- [ ] Remove `_detect_webhook_format()` and `verify_legacy_commerce_signature()` from webhook handler
- [ ] Remove `_handle_legacy_commerce_webhook()` from webhook handler
- [ ] Remove legacy event types and `handle_charge_confirmed()` from webhook service
- [ ] Update webhook endpoint to only accept `X-Hook0-Signature`

---

## 13. IP Allowlisting

- [ ] If using CDP API key IP allowlisting, ensure all API server IPs are added
- [ ] If behind a load balancer, verify the outbound IP (NAT gateway) is allowlisted

---

## 14. Monitoring & Alerting

Ensure alerts are configured for these log event types:

**Payment Link (new):**
- `coinbase_pl_webhook_not_configured` — Secret missing (critical)
- `coinbase_pl_webhook_invalid_signature` — Signature mismatch (security)
- `coinbase_pl_webhook_replay` — Replay attack attempt (security)
- `coinbase_payment_link_error` — API call failures (operational)

**Legacy Commerce (transition period):**
- `coinbase_legacy_webhook_not_configured` — Legacy secret missing
- `coinbase_legacy_webhook_invalid_signature` — Legacy signature mismatch

---

## 15. Documentation References

- [Migration Overview](https://docs.cdp.coinbase.com/coinbase-business/payment-link-apis/migrate/overview)
- [API & Schema Mapping](https://docs.cdp.coinbase.com/coinbase-business/payment-link-apis/migrate/api-schema-mapping)
- [Payment Link API Reference](https://docs.cdp.coinbase.com/api-reference/business-api/rest-api/payment-links/introduction)
- [CDP API Key Auth](https://docs.cdp.coinbase.com/api-reference/v2/authentication)
- [Webhook Docs](https://docs.cdp.coinbase.com/coinbase-business/payment-link-apis/webhooks)
- [Sandbox Environment](https://docs.cdp.coinbase.com/coinbase-business/payment-link-apis/sandbox)
- [Testnet Faucet (Base Sepolia USDC)](https://portal.cdp.coinbase.com/products/faucet)
- [Migration FAQ](https://docs.cdp.coinbase.com/coinbase-business/payment-link-apis/migrate/faq)
