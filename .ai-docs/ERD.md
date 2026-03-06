# Morpheus Marketplace API — Database ERD

Entity relationship diagram for the PostgreSQL schema (SQLAlchemy models in `src/db/models/`).

## Mermaid diagram

```mermaid
erDiagram
    users {
        int id PK
        string cognito_user_id UK "Cognito sub"
        string email UK
        string name
        boolean is_active
        datetime created_at
        datetime updated_at
    }

    api_keys {
        int id PK
        string key_prefix
        string hashed_key
        text encrypted_key
        int encryption_version
        int user_id FK
        string name
        datetime created_at
        datetime last_used_at
        boolean is_active
        boolean is_default
    }

    chats {
        string id PK "UUID"
        int user_id FK
        string title
        datetime created_at
        datetime updated_at
        boolean is_archived
    }

    messages {
        string id PK "UUID"
        string chat_id FK
        enum role "user|assistant"
        text content
        int sequence
        datetime created_at
        int tokens
    }

    wallet_links {
        int id PK
        int user_id FK
        string wallet_address UK "0x+40 hex"
        numeric staked_amount "wei"
        datetime linked_at
        datetime updated_at
    }

    wallet_nonces {
        int id PK
        int user_id FK
        string nonce UK
        string wallet_address
        datetime created_at
        datetime expires_at
        datetime consumed
    }

    credits_ledger {
        uuid id PK
        int user_id FK
        string currency
        enum status "pending|posted|voided"
        enum entry_type "purchase|staking_refresh|usage_hold|usage_charge|refund|adjustment"
        numeric amount_paid
        numeric amount_staking
        text idempotency_key UK
        uuid related_entry_id FK "self"
        string payment_source
        string external_transaction_id
        jsonb payment_metadata
        text request_id
        int api_key_id FK
        text model_name
        string model_id
        text endpoint
        int tokens_input
        int tokens_output
        datetime created_at
        datetime updated_at
    }

    credit_account_balances {
        int user_id PK,FK
        numeric paid_posted_balance
        numeric paid_pending_holds
        numeric staking_daily_amount
        date staking_refresh_date
        numeric staking_available
        boolean is_staker
        boolean allow_overage
        datetime created_at
        datetime updated_at
    }

    routed_sessions {
        string id PK "blockchain session ID"
        string model_name
        string model_id
        string state "OPEN|CLOSING|CLOSED|FAILED|EXPIRED"
        int active_requests
        datetime created_at
        datetime updated_at
        datetime last_used_at
        datetime expires_at
        string endpoint
        string error_reason
    }

    users ||--o{ api_keys : "has"
    users ||--o{ chats : "has"
    users ||--o{ wallet_links : "has"
    users ||--o{ wallet_nonces : "has"
    users ||--o| credit_account_balances : "has one"
    users ||--o{ credits_ledger : "has"

    chats ||--o{ messages : "has"

    api_keys ||--o{ credits_ledger : "used in"

    credits_ledger }o--o| credits_ledger : "related_entry"
```

## Table summary

| Table | Purpose |
|-------|---------|
| **users** | Accounts; identity via `cognito_user_id` (Cognito = source of truth for email). |
| **api_keys** | API keys per user; optional encrypted storage, `last_used_at` for activity. |
| **chats** | Conversation containers; one per user, cascade delete with user. |
| **messages** | Messages in a chat; `role` (user/assistant), `sequence`, optional `tokens`. |
| **wallet_links** | Links Web3 wallets to users; one wallet globally unique; `staked_amount` (wei) for credits. |
| **wallet_nonces** | One-time nonces for wallet linking; expire after 5 min, `consumed` when used. |
| **credits_ledger** | All credit movements (purchase, staking_refresh, usage_hold/charge, refund, adjustment); split `amount_paid` / `amount_staking`; optional link to `api_keys` and self (`related_entry_id`). |
| **credit_account_balances** | Per-user balance cache (paid + staking buckets, `is_staker`, `allow_overage`). |
| **routed_sessions** | Session routing state by model; no FK to users; lifecycle OPEN → CLOSING → CLOSED. |

## Key relationships

- **User** is the central entity: has many api_keys, chats, wallet_links, wallet_nonces; has one credit_account_balances; has many credits_ledger rows.
- **Chat** belongs to one user; has many **messages** (cascade delete).
- **credits_ledger** can reference **api_keys** (for usage_charge) and **credits_ledger** (related_entry_id for refunds/linked entries).
- **routed_sessions** is standalone (model/session lifecycle only).

## Enums

- **messages.role**: `user`, `assistant`
- **credits_ledger.status**: `pending`, `posted`, `voided`
- **credits_ledger.entry_type**: `purchase`, `staking_refresh`, `usage_hold`, `usage_charge`, `refund`, `adjustment`
- **routed_sessions.state**: `OPEN`, `CLOSING`, `CLOSED`, `FAILED`, `EXPIRED`

---

*Generated from `src/db/models/`. Email is sourced from Cognito; DB stores `cognito_user_id`.*
