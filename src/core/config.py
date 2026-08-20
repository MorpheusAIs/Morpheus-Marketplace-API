import os
from decimal import Decimal
from typing import List, Union, Optional, Any
from pydantic_settings import BaseSettings
from pydantic import PostgresDsn, Field, AnyHttpUrl, field_validator, model_validator
from dotenv import load_dotenv

# Load .env file variables
load_dotenv()

# Environments considered safe for development conveniences (placeholder
# secrets, missing Cognito config, permissive CORS). Anything else — production,
# staging, or an unrecognized value — is treated as production-like and must be
# fully, explicitly configured.
NON_PRODUCTION_ENVIRONMENTS = {"local", "development", "dev", "test"}

# Known placeholder that used to ship as the ENCRYPTION_SECRET_KEY default.
# Never acceptable outside local/test: it is public in the repo history.
_PLACEHOLDER_ENCRYPTION_KEY = "encryption_secret_change_me"

class Settings(BaseSettings):
    # Project Settings
    PROJECT_NAME: str = "Morpheus API Gateway"
    API_V1_STR: str = "/api/v1"
    
    # Base URL - set by Terraform based on environment
    BASE_URL: str = Field(default=os.getenv("BASE_URL", "http://localhost:8000"))
    
    # Environment detection for CORS configuration
    ENVIRONMENT: str = Field(default=os.getenv("ENVIRONMENT", "development"))
    
    # CORS Settings - explicit allowlist for credential-safe CORS
    CORS_ALLOWED_ORIGINS: List[str] = Field(
        default_factory=lambda: []  # Empty means auto-detect
    )
    
    # Development CORS origins (for local development)
    CORS_DEV_ORIGINS: str = Field(
        default=os.getenv(
            "CORS_DEV_ORIGINS", 
            "http://localhost:3000,http://localhost:8080,http://127.0.0.1:3000,http://127.0.0.1:8080"
        )
    )
    
    # Legacy CORS setting (deprecated - use CORS_ALLOWED_ORIGINS instead)
    BACKEND_CORS_ORIGINS: Union[List[str], str] = Field(default="*")
    
    @field_validator("CORS_ALLOWED_ORIGINS", mode="before")
    def parse_cors_origins(cls, v) -> List[str]:
        """Parse comma-separated CORS origins into a list with environment awareness"""
        # Get environment from the current values being validated
        environment = os.getenv("ENVIRONMENT", "development").lower()
        
        # Check if CORS_ALLOWED_ORIGINS environment variable is set
        env_cors_origins = os.getenv("CORS_ALLOWED_ORIGINS", "")
        
        # Handle different input types
        if env_cors_origins and env_cors_origins.strip():
            # Environment variable is set, use it
            origins = [origin.strip() for origin in env_cors_origins.split(",") if origin.strip()]
        elif isinstance(v, list) and v:
            # Already a list with values, filter out empty strings
            origins = [origin.strip() for origin in v if origin and origin.strip()]
        elif isinstance(v, str) and v.strip():
            # String input, split by comma
            origins = [origin.strip() for origin in v.split(",") if origin.strip()]
        else:
            # Empty or None input, use auto-detection
            origins = []
        
        # If no explicit origins provided, auto-detect based on environment
        if not origins:
            # Auto-detect based on environment
            if environment in ["production", "prod", "prd"]:
                origins = [
                    "https://openbeta.mor.org",
                    "https://api.mor.org",
                    "https://app.mor.org"
                ]
            elif environment in ["development", "dev", "test", "staging"]:
                origins = [
                    # Production origins (for cross-env testing)
                    "https://openbeta.mor.org",
                    "https://api.mor.org",
                    "https://app.mor.org",
                    # Development origins
                    "https://openbeta.dev.mor.org",
                    "https://api.dev.mor.org",
                    "https://app.dev.mor.org",
                    # Local development origins
                    "http://localhost:3000",
                    "http://localhost:8080",
                    "http://127.0.0.1:3000",
                    "http://127.0.0.1:8080"
                ]
            else:
                # Unknown environment - use safe defaults
                origins = [
                    "https://openbeta.mor.org",
                    "https://api.mor.org",
                    "https://app.mor.org"
                ]
        
        # Add development origins if CORS_DEV_ORIGINS is set
        dev_origins_str = os.getenv("CORS_DEV_ORIGINS", "")
        if dev_origins_str and environment != "production":
            dev_origins = [origin.strip() for origin in dev_origins_str.split(",") if origin.strip()]
            # Add dev origins that aren't already in the list
            for dev_origin in dev_origins:
                if dev_origin not in origins:
                    origins.append(dev_origin)
        
        # Validate that we don't have wildcards with credentials
        for origin in origins:
            if origin == "*":
                raise ValueError(
                    "CORS_ALLOWED_ORIGINS cannot contain '*' when credentials are enabled. "
                    f"Use specific origins. Current environment: {environment}"
                )
        
        # Remove duplicates while preserving order
        seen = set()
        unique_origins = []
        for origin in origins:
            if origin not in seen:
                seen.add(origin)
                unique_origins.append(origin)
        
        return unique_origins
    
    @field_validator("BACKEND_CORS_ORIGINS", mode="before")
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> Union[List[str], str]:
        if isinstance(v, str) and not v.startswith("["):
            if v == "":
                # Return ["*"] to allow all origins if empty string
                return ["*"]
            return [i.strip() for i in v.split(",") if i.strip()]
        elif isinstance(v, list):
            return v
        return v
    
    # Database Connection - Using default port 5432 to match running Docker container
    DATABASE_URL: str = Field(default=os.getenv("DATABASE_URL"))
    
    # Database Settings (placeholders for Docker)
    DB_USER: str = Field(default=os.getenv("POSTGRES_USER", "morpheus_user"))
    DB_PASSWORD: str = Field(default=os.getenv("POSTGRES_PASSWORD", "secure_password_here"))
    DB_NAME: str = Field(default=os.getenv("POSTGRES_DB", "morpheus_db"))
    
    # SQLAlchemy Connection Pool Settings
    # Adjust these based on your load requirements and RDS max_connections setting
    DB_POOL_SIZE: int = Field(default=int(os.getenv("DB_POOL_SIZE", "20")))
    DB_MAX_OVERFLOW: int = Field(default=int(os.getenv("DB_MAX_OVERFLOW", "30")))
    DB_POOL_TIMEOUT: int = Field(default=int(os.getenv("DB_POOL_TIMEOUT", "30")))
    DB_POOL_RECYCLE: int = Field(default=int(os.getenv("DB_POOL_RECYCLE", "3600")))
    DB_POOL_PRE_PING: bool = Field(default=os.getenv("DB_POOL_PRE_PING", "true").lower() == "true")
    
    DEFAULT_BALANCE_AMOUNT: int = Field(default=int(os.getenv("DEFAULT_BALANCE_AMOUNT", "9")))

    # One-time signup bonus credited as an explicit ledger entry on first login.
    # Independent of DEFAULT_BALANCE_AMOUNT — both can be tuned separately.
    # Set SIGNUP_BONUS_AMOUNT=0 to disable the bonus entirely.
    # Set SIGNUP_BONUS_IP_WINDOW_HOURS=0 to disable IP-based rate limiting.
    SIGNUP_BONUS_AMOUNT: Decimal = Field(default=Decimal(os.getenv("SIGNUP_BONUS_AMOUNT", "1")))
    SIGNUP_BONUS_IP_WINDOW_HOURS: int = Field(default=int(os.getenv("SIGNUP_BONUS_IP_WINDOW_HOURS", "24")))

    # API Key Encryption.
    # No baked-in default: production-like environments must set this explicitly
    # (enforced in enforce_production_configuration below). Local/dev/test fall
    # back to a placeholder there so local boots keep working.
    ENCRYPTION_SECRET_KEY: str = Field(default=os.getenv("ENCRYPTION_SECRET_KEY", ""))

    # Proxy Router Settings
    PROXY_ROUTER_URL: str = Field(default=os.getenv("PROXY_ROUTER_URL", ""))
    PROXY_ROUTER_USERNAME: str = Field(default=os.getenv("PROXY_ROUTER_USERNAME", ""))
    PROXY_ROUTER_PASSWORD: str = Field(default=os.getenv("PROXY_ROUTER_PASSWORD", ""))
    PROXY_ROUTER_CHAT_TIMEOUT: float = Field(default=float(os.getenv("PROXY_ROUTER_CHAT_TIMEOUT", "300.0")))
    PROXY_ROUTER_STREAM_TIMEOUT: float = Field(default=float(os.getenv("PROXY_ROUTER_STREAM_TIMEOUT", "300.0")))
    CHAT_FAILOVER_ENABLED: bool = Field(default=os.getenv("CHAT_FAILOVER_ENABLED", "true").lower() == "true")

    # AWS settings (credentials come from ECS task role; no explicit keys needed)
    AWS_REGION: str = os.getenv("AWS_REGION", "us-east-2")
    
    # AWS Cognito Settings.
    # No hardcoded pool/client defaults: a misconfigured environment must not
    # silently authenticate against the production user pool. Production-like
    # environments fail startup when these are unset (see
    # enforce_production_configuration); local/dev/test simply leave JWT auth
    # unconfigured until set.
    COGNITO_USER_POOL_ID: str = Field(default=os.getenv("COGNITO_USER_POOL_ID", ""))
    COGNITO_CLIENT_ID: str = Field(default=os.getenv("COGNITO_CLIENT_ID", ""))
    COGNITO_REGION: str = Field(default=os.getenv("COGNITO_REGION", "us-east-2"))
    COGNITO_DOMAIN: str = Field(default=os.getenv("COGNITO_DOMAIN", "auth.mor.org"))
    # Derived from the final region/pool values in enforce_production_configuration
    # unless overridden explicitly.
    COGNITO_JWKS_URL: str = Field(default=os.getenv("COGNITO_JWKS_URL", ""))
    
    # Session Routing Service Configuration
    # Interval in seconds for automated activity loop (session scaling)
    SESSION_AUTOMATION_INTERVAL_SECONDS: int = Field(default=int(os.getenv("SESSION_AUTOMATION_INTERVAL_SECONDS", "30")))
    # Grace period before closing idle sessions (prevents thrashing). Keep this
    # LESS than SESSION_DEFAULT_DURATION_SECONDS so idle sessions are early-closed
    # rather than riding to natural expiry. Under SessionRouter day-lock, unused
    # stake returns on close; used (wall-clock) stipend goes to userStakesOnHold
    # until the next UTC day — so shorter idle lifetime reduces day-locked MOR.
    SESSION_IDLE_GRACE_SECONDS: int = Field(default=int(os.getenv("SESSION_IDLE_GRACE_SECONDS", "300")))
    # Default session duration when creating new sessions (in seconds)
    SESSION_DEFAULT_DURATION_SECONDS: int = Field(default=int(os.getenv("SESSION_DEFAULT_DURATION_SECONDS", "1800")))
    # Buffer (seconds) added on top of the ACTUAL on-chain endsAt when setting a
    # routed session's DB expires_at. The cleanup sweep closes a session once
    # now >= expires_at; anchoring to the real on-chain endsAt (read back after
    # open) plus this small buffer avoids closing a few seconds BEFORE endsAt
    # when a session rides to natural expiry. Keep small — just enough to clear
    # host/chain clock skew and block/mining latency. (Day-lock: unused stake
    # still returns on close; used stipend is held until the next UTC day
    # regardless of early vs late close.)
    SESSION_EXPIRY_BUFFER_SECONDS: int = Field(default=int(os.getenv("SESSION_EXPIRY_BUFFER_SECONDS", "60")))
    # Comma-separated list of preferred (warm-pool) model IDs. Automation keeps
    # at least one OPEN session for each; soft-cap uses SESSION_SOFT_CAP_WARM.
    SESSION_PREFERRED_MODELS: str = Field(default=os.getenv("SESSION_PREFERRED_MODELS", ""))

    # --- Per-model OPEN-session soft caps ------------------------------------
    # Caps concurrent OPEN sessions per model so the shared consumer wallet
    # cannot be exhausted by unbounded scale-up. When at cap with no idle
    # session to claim, route_request raises SessionPoolBusyError (HTTP 429).
    # 0 DISABLES that cap (unlimited) — ships inert; turn on per-env via secrets.
    # Warm (preferred) models use SESSION_SOFT_CAP_WARM; everything else uses
    # SESSION_SOFT_CAP_DEFAULT.
    SESSION_SOFT_CAP_WARM: int = Field(default=int(os.getenv("SESSION_SOFT_CAP_WARM", "0")))
    SESSION_SOFT_CAP_DEFAULT: int = Field(default=int(os.getenv("SESSION_SOFT_CAP_DEFAULT", "0")))
    # Retry-After hint (seconds) returned with SessionPoolBusyError / HTTP 429.
    SESSION_SOFT_CAP_RETRY_AFTER_SECONDS: int = Field(
        default=int(os.getenv("SESSION_SOFT_CAP_RETRY_AFTER_SECONDS", "15"))
    )

    # --- Warm-model MOR low-water mark ---------------------------------------
    # Liquid consumer-wallet MOR floor reserved for warm (preferred) session
    # opens. When balance <= this value, non-warm models cannot open new
    # on-chain sessions (idle claims still succeed). Warm models skip the gate.
    # 0 DISABLES the check — ships inert; turn on per-env via secrets
    # (prod target ~15000 MOR). Distinct from soft caps (session count vs MOR).
    SESSION_MOR_LOW_WATER_MARK_MOR: float = Field(
        default=float(os.getenv("SESSION_MOR_LOW_WATER_MARK_MOR", "0"))
    )
    # Short TTL for /blockchain/balance reads so open bursts don't hammer the
    # C-Node. Per-replica only.
    SESSION_MOR_BALANCE_CACHE_SECONDS: float = Field(
        default=float(os.getenv("SESSION_MOR_BALANCE_CACHE_SECONDS", "15"))
    )

    # --- Curated allowlist (APIGW taster catalog) -----------------------------
    # Comma-separated blockchain IDs that may open on the hosted gateway after
    # alias rewrite. Empty DISABLES (full catalog). Non-empty: anything else
    # resolves to HTTP 503 model_unavailable + P2P off-ramp.
    SESSION_ALLOWED_MODEL_IDS: str = Field(
        default=os.getenv("SESSION_ALLOWED_MODEL_IDS", "")
    )
    # Deterministic family aliases applied BEFORE resolve/allowlist.
    # Format: comma-separated "alias=target" (also accepts "->" / "→").
    # Keys matched case-insensitively. Never map :web/:tee → base when a twin
    # exists — keep Venice / SecretVM feature paths.
    # Prefer SESSION_ROUTING_POLICY_JSON.aliases when that env is set.
    SESSION_MODEL_ALIASES: str = Field(
        default=os.getenv("SESSION_MODEL_ALIASES", "")
    )
    # Open-native routing policy JSON: aliases, preferences, deny, max_stake_mor.
    # See Morpheus-Infra session_routing_policy + APIGW_MOR_LANES.md.
    SESSION_ROUTING_POLICY_JSON: str = Field(
        default=os.getenv("SESSION_ROUTING_POLICY_JSON", "")
    )

    # --- Max-bid PPS hard gate (standard lane) --------------------------------
    # Refuse opens (and idle claims) when the model's HIGHEST rated bid PPS is
    # at/above this MOR/sec threshold. Equivalent to ~175 MOR escrow for a
    # 30-minute session under day-lock stake factor ~338:
    #   175 / (1800 * 338) ≈ 0.00028764
    # Warm (SESSION_PREFERRED_MODELS) and premium showcase IDs skip this gate.
    # 0 DISABLES the gate — ships inert; enable per-env via secrets.
    # Prefer SESSION_ALLOWED_MODEL_IDS as the primary catalog control; keep PPS
    # as an optional backup when allowlist is empty.
    SESSION_MAX_BID_PPS_MOR: float = Field(
        default=float(os.getenv("SESSION_MAX_BID_PPS_MOR", "0"))
    )

    # --- Premium showcase lane ------------------------------------------------
    # Comma-separated model blockchain IDs exempt from the PPS gate but limited
    # by SESSION_PREMIUM_DAILY_BUDGET_MOR (day-locked MOR, UTC day). Gate uses
    # Redis actuals (from close-tx / pro-rata) + SUM(stake_mor) on OPEN/CLOSING
    # premium rows as holds. Soft cap still applies (DEFAULT). Empty / budget
    # 0 disables the premium lane (IDs would then hit the PPS gate like
    # standard models if listed).
    SESSION_PREMIUM_MODEL_IDS: str = Field(
        default=os.getenv("SESSION_PREMIUM_MODEL_IDS", "")
    )
    SESSION_PREMIUM_DAILY_BUDGET_MOR: float = Field(
        default=float(os.getenv("SESSION_PREMIUM_DAILY_BUDGET_MOR", "0"))
    )
    # Kept for stake-sizing / expensive-tier docs; premium daylock meter no
    # longer uses max_pps × elapsed × factor (that inflated Redis vs chain).
    SESSION_STAKE_FACTOR: float = Field(
        default=float(os.getenv("SESSION_STAKE_FACTOR", "338"))
    )
    # Optional: decode close-tx MOR Transfer(to=consumer) for receipt-true
    # daylock (= stake − returned). When unset, meter falls back to
    # stake × lived / sched from getSession. Base mainnet MOR + C-Node wallet.
    MOR_TOKEN_ADDRESS: str | None = Field(default=os.getenv("MOR_TOKEN_ADDRESS"))
    SESSION_CONSUMER_WALLET_ADDRESS: str | None = Field(
        default=os.getenv("SESSION_CONSUMER_WALLET_ADDRESS")
    )

    # --- Expensive-model session tier ---------------------------------------
    # The on-chain stake pulled at openSession scales linearly with duration and
    # is amplified by (total MOR supply / today's emissions budget), so a
    # high-priced model at the normal duration can stake enough MOR to exhaust
    # the shared consumer wallet and bounce concurrent opens. This tier gives
    # models with ANY rated bid >= a cutoff (max(bid.PricePerSecond) >= cutoff)
    # a shorter duration (smaller per-session stake -> more concurrent premium
    # sessions) with their own idle grace, decoupled from the global session
    # settings above. Classifying on the HIGHEST bid — not the lowest — matters
    # because HA failover can re-land a session on the model's priciest peer:
    # a cheap underbidder must not earn the model the long default stake.
    #
    # Cutoff is MOR per second (a bid's PricePerSecond / 1e18). 0 DISABLES the
    # tier entirely (every model uses the global SESSION_* settings) — the
    # feature ships inert and is turned on per-environment via secrets.
    SESSION_EXPENSIVE_CUTOFF_MOR_PER_SECOND: float = Field(default=float(os.getenv("SESSION_EXPENSIVE_CUTOFF_MOR_PER_SECOND", "0")))
    # Session duration (seconds) for expensive models. Kept short to bound the
    # amplified on-chain stake per session.
    SESSION_EXPENSIVE_DEFAULT_DURATION_SECONDS: int = Field(default=int(os.getenv("SESSION_EXPENSIVE_DEFAULT_DURATION_SECONDS", "1200")))
    # Idle grace (seconds) for expensive models, overriding SESSION_IDLE_GRACE_SECONDS.
    # Keep this LESS than SESSION_EXPENSIVE_DEFAULT_DURATION_SECONDS so idle
    # expensive sessions early-close (day-lock: unused returns; used stipend is
    # held until next UTC day — shorter lifetime reduces day-locked MOR).
    SESSION_EXPENSIVE_IDLE_GRACE_SECONDS: int = Field(default=int(os.getenv("SESSION_EXPENSIVE_IDLE_GRACE_SECONDS", "300")))
    # Adaptive on-chain wallet throttle: after a nonce conflict is observed on a
    # session open/close, on-chain ops serialize on the wallet lock for this many
    # seconds (sliding; each new conflict re-arms it). 0 disables the throttle
    # (always concurrent). The happy path is never serialized.
    SESSION_ONCHAIN_THROTTLE_COOLDOWN_SECONDS: float = Field(default=float(os.getenv("SESSION_ONCHAIN_THROTTLE_COOLDOWN_SECONDS", "20")))
    # Open-time bid walk: when InitiateSession fails with provider capacity /
    # unreachable, try the next cheapest healthy-under-fuse bid (omit accumulated
    # providers so we never ping-pong). Cap attempts to bound on-chain load.
    SESSION_OPEN_BID_WALK_MAX_ATTEMPTS: int = Field(
        default=int(os.getenv("SESSION_OPEN_BID_WALK_MAX_ATTEMPTS", "3"))
    )
    # After a capacity/unreachable open failure, skip that model+provider for
    # this many seconds (per-replica) so subsequent opens start further down
    # the candidate list instead of re-hammering the same full host.
    SESSION_OPEN_PROVIDER_COOLDOWN_SECONDS: float = Field(
        default=float(os.getenv("SESSION_OPEN_PROVIDER_COOLDOWN_SECONDS", "120"))
    )

    # Direct Model Fetching Settings (replaces model sync)
    ACTIVE_MODELS_URL: str = Field(default=os.getenv("ACTIVE_MODELS_URL", "https://active.dev.mor.org/active_models.json"))
    DEFAULT_FALLBACK_MODEL: str = Field(default=os.getenv("DEFAULT_FALLBACK_MODEL", "mistral-31-24b"))
    DEFAULT_FALLBACK_EMBEDDINGS_MODEL: str = Field(default=os.getenv("DEFAULT_FALLBACK_EMBEDDINGS_MODEL", "text-embedding-bge-m3"))
    DEFAULT_FALLBACK_TTS_MODEL: str = Field(default=os.getenv("DEFAULT_FALLBACK_TTS_MODEL", "tts-kokoro"))
    DEFAULT_FALLBACK_STT_MODEL: str = Field(default=os.getenv("DEFAULT_FALLBACK_STT_MODEL", "whisper-1"))
    
    # Billing Admin Settings
    # Secret key required for admin billing operations (staking settings, manual topups)
    # If not set, admin endpoints will be disabled
    BILLING_ADMIN_SECRET: str | None = Field(default=os.getenv("BILLING_ADMIN_SECRET"))
    
    # Builders API Settings (for MOR staking data)
    # Used to fetch staker information for credit allocation
    BUILDERS_API_URL: str = Field(
        default=os.getenv("BUILDERS_API_URL", "https://dashboard.mor.org/api")
    )
    BUILDERS_SUBNET_ID: str = Field(
        default=os.getenv("BUILDERS_SUBNET_ID", "")
    )
    
    # CoinCap API Settings (for MOR price data)
    # Optional API key for higher rate limits: https://pro.coincap.io/api-docs
    COINCAP_API_KEY: str | None = Field(default=os.getenv("COINCAP_API_KEY"))
    
    # Staking Credits Adjustment Factor
    # Multiplier applied to final daily credits calculation for manual tuning
    # Formula: daily_credits = stake_share * today_emission * mor_price * ADJUSTMENT_FACTOR
    # Default: 1.0 (no adjustment)
    STAKING_CREDITS_ADJUSTMENT_FACTOR: str = Field(
        default=os.getenv("STAKING_CREDITS_ADJUSTMENT_FACTOR", "1.0")
    )
    
    # Stripe Settings
    # Required for processing Stripe payments and webhooks
    STRIPE_SECRET_KEY: str | None = Field(default=os.getenv("STRIPE_SECRET_KEY"))
    STRIPE_WEBHOOK_SECRET: str | None = Field(default=os.getenv("STRIPE_WEBHOOK_SECRET"))
    
    # Coinbase Commerce Settings (Legacy - kept for backward compatibility)
    # Required for processing legacy Coinbase Commerce charge webhooks
    COINBASE_COMMERCE_WEBHOOK_SECRET: str | None = Field(default=os.getenv("COINBASE_COMMERCE_WEBHOOK_SECRET"))
    
    # Coinbase Business / CDP Settings
    # Payment Link webhook signature verification secret
    # Secret from metadata.secret returned when creating a webhook subscription
    # See: https://docs.cdp.coinbase.com/coinbase-business/payment-link-apis/webhooks
    COINBASE_PAYMENT_LINK_WEBHOOK_SECRET: str | None = Field(default=os.getenv("COINBASE_PAYMENT_LINK_WEBHOOK_SECRET"))

    # CDP API Key credentials for Payment Link CRUD operations
    # Key ID: UUID from the CDP portal (Secret API Key tab)
    # Key Secret: base64-encoded secret from the CDP portal
    # See: https://docs.cdp.coinbase.com/api-reference/v2/authentication
    CDP_API_KEY_ID: str | None = Field(default=os.getenv("CDP_API_KEY_ID"))
    CDP_API_KEY_SECRET: str | None = Field(default=os.getenv("CDP_API_KEY_SECRET"))
    # Set to true to use the Coinbase sandbox environment (no real transactions)
    # See: https://docs.cdp.coinbase.com/coinbase-business/payment-link-apis/sandbox
    CDP_SANDBOX: bool = Field(default=os.getenv("CDP_SANDBOX", "false").lower() == "true")
    
    # Web3 Provider Settings (optional - enables EIP-1271 smart contract wallet verification)
    # If not set, only EOA wallets will be supported
    WEB3_PROVIDER_URL: str | None = Field(default=os.getenv("WEB3_PROVIDER_URL"))
    # SIWE (Sign-In with Ethereum) settings
    SIWE_DOMAIN: str = Field(default=os.getenv("SIWE_DOMAIN", "app.mor.org"))
    SIWE_URI: str = Field(default=os.getenv("SIWE_URI", "https://app.mor.org"))
    SIWE_CHAIN_ID: int = Field(default=int(os.getenv("SIWE_CHAIN_ID", "8453")))  # Default: Base
    
    # Legacy Model Sync Settings (deprecated - kept for compatibility)
    MODEL_SYNC_ON_STARTUP: bool = Field(default=False)  # Disabled by default
    MODEL_SYNC_INTERVAL_HOURS: int = Field(default=int(os.getenv("MODEL_SYNC_INTERVAL_HOURS", "1")))
    MODEL_SYNC_ENABLED: bool = Field(default=False)  # Disabled by default
    
    # Redis Settings (for rate limiting and caching)
    REDIS_URL: str = Field(default=os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    REDIS_MAX_CONNECTIONS: int = Field(default=int(os.getenv("REDIS_MAX_CONNECTIONS", "20")))
    REDIS_SOCKET_TIMEOUT: float = Field(default=float(os.getenv("REDIS_SOCKET_TIMEOUT", "5.0")))
    REDIS_SOCKET_CONNECT_TIMEOUT: float = Field(default=float(os.getenv("REDIS_SOCKET_CONNECT_TIMEOUT", "5.0")))
    # Rate-limit checks sit on the hot path and fail open, so they use much
    # tighter Redis timeouts than the cache: a limiter call must never be allowed
    # to block for seconds before degrading.
    RATE_LIMIT_REDIS_SOCKET_TIMEOUT: float = Field(default=float(os.getenv("RATE_LIMIT_REDIS_SOCKET_TIMEOUT", "1.0")))
    RATE_LIMIT_REDIS_SOCKET_CONNECT_TIMEOUT: float = Field(default=float(os.getenv("RATE_LIMIT_REDIS_SOCKET_CONNECT_TIMEOUT", "0.5")))
    
    # Cache Settings
    # Enable Redis caching for API keys, users, sessions, and JWKS
    # When disabled, all requests will hit the database directly
    # Default: false (opt-in for safety - requires explicit enablement)
    CACHE_ENABLED: bool = Field(default=os.getenv("CACHE_ENABLED", "false").lower() == "true")
    
    # Hold Reconciliation Settings
    # Interval between reconciliation sweeps (seconds). Default: 10 minutes.
    HOLD_RECONCILIATION_INTERVAL_SECONDS: int = Field(default=int(os.getenv("HOLD_RECONCILIATION_INTERVAL_SECONDS", "600")))
    # Maximum age of a pending hold before it is auto-voided (seconds).  Default: 3600s / 60 min
    HOLD_MAX_PENDING_SECONDS: int = Field(default=int(os.getenv("HOLD_MAX_PENDING_SECONDS", "3600")))
    
    # Rate Limiting Settings
    # Enable/disable rate limiting globally
    RATE_LIMIT_ENABLED: bool = Field(default=os.getenv("RATE_LIMIT_ENABLED", "true").lower() == "true")
    # When true, a degraded limiter (Redis outage, circuit breaker open)
    # REJECTS requests (429 with Retry-After) instead of letting them through
    # unmetered. Default false preserves the availability-first behavior; enable
    # per environment where unthrottled cost amplification is the bigger risk.
    RATE_LIMIT_FAIL_CLOSED: bool = Field(default=os.getenv("RATE_LIMIT_FAIL_CLOSED", "false").lower() == "true")
    # Rate limit defaults and model groups are loaded from models/{env}_rate_limit.json
    # Model pricing is loaded from models/{env}_model_price.json

    @property
    def is_production_like(self) -> bool:
        """True for production, staging, or any unrecognized environment.

        Fail-closed: only explicitly local/dev/test environments get development
        conveniences (placeholder secrets, permissive CORS, debug endpoints).
        """
        return self.ENVIRONMENT.lower() not in NON_PRODUCTION_ENVIRONMENTS

    @model_validator(mode="after")
    def enforce_production_configuration(self) -> "Settings":
        """Fail startup on unsafe configuration outside local/dev/test.

        ENCRYPTION_SECRET_KEY must be explicitly set (and not the public
        placeholder) in production-like environments — otherwise every stored
        API key is encrypted with a key published in the source repo.
        Cognito pool/client IDs must be explicitly set in production-like
        environments — otherwise a misconfigured deploy silently authenticates
        against the wrong (previously: hardcoded production) user pool.
        """
        if self.is_production_like:
            errors = []
            if not self.ENCRYPTION_SECRET_KEY or self.ENCRYPTION_SECRET_KEY == _PLACEHOLDER_ENCRYPTION_KEY:
                errors.append(
                    "ENCRYPTION_SECRET_KEY must be explicitly set to a strong secret"
                )
            if not self.COGNITO_USER_POOL_ID:
                errors.append("COGNITO_USER_POOL_ID must be explicitly set")
            if not self.COGNITO_CLIENT_ID:
                errors.append("COGNITO_CLIENT_ID must be explicitly set")
            if errors:
                raise ValueError(
                    f"Refusing to start with ENVIRONMENT={self.ENVIRONMENT!r}: "
                    + "; ".join(errors)
                    + ". Set the variables or run with a local/dev/test ENVIRONMENT."
                )
        elif not self.ENCRYPTION_SECRET_KEY:
            # Local/dev/test convenience only — never reachable in production-like
            # environments (guard above).
            self.ENCRYPTION_SECRET_KEY = _PLACEHOLDER_ENCRYPTION_KEY

        if not self.COGNITO_JWKS_URL and self.COGNITO_USER_POOL_ID:
            self.COGNITO_JWKS_URL = (
                f"https://cognito-idp.{self.COGNITO_REGION}.amazonaws.com/"
                f"{self.COGNITO_USER_POOL_ID}/.well-known/jwks.json"
            )
        return self

    class Config:
        env_file = ".env"
        env_file_encoding = 'utf-8'
        case_sensitive = True
        
        # Allow extra fields from environment variables
        extra = "ignore"
        
        # Allow env variables to be parsed as complex types
        validate_assignment = True

settings = Settings() 