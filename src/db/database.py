from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import text
from typing import AsyncGenerator
from contextlib import asynccontextmanager
from src.core.config import settings

# Create async engine instance with explicit pool configuration
# Configured for high-concurrency scenarios (rapid sequential requests)
# Pool settings are now configurable via environment variables
engine = create_async_engine(
    str(settings.DATABASE_URL), # Ensure URL is a string
    pool_pre_ping=settings.DB_POOL_PRE_PING,
    echo=False, # Set to True for debugging SQL queries
    # Connection pool settings for handling rapid sequential requests
    pool_size=settings.DB_POOL_SIZE,              # Base pool size - configurable via DB_POOL_SIZE
    max_overflow=settings.DB_MAX_OVERFLOW,        # Max overflow connections - configurable via DB_MAX_OVERFLOW
    pool_timeout=settings.DB_POOL_TIMEOUT,        # Wait timeout for connection - configurable via DB_POOL_TIMEOUT
    pool_recycle=settings.DB_POOL_RECYCLE,        # Recycle connections - configurable via DB_POOL_RECYCLE
    pool_reset_on_return='rollback',              # Reset connection state on return
)

# Create sessionmaker
# expire_on_commit=False prevents detached instance errors in FastAPI background tasks
AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# Base class for declarative models
Base = declarative_base()

# Context manager version for manual usage (async with get_db_context() as db:)
@asynccontextmanager
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Get a database session as a context manager.
    
    Usage - As context manager (short-lived, recommended for most cases):
        async with get_db() as db:
            result = await db.execute(query)
            # Connection released when context exits
    
    This is the recommended approach for session management to avoid
    connection pool exhaustion during long-running operations.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

# Cluster-wide leader election for in-process background loops
@asynccontextmanager
async def advisory_xact_lock(lock_name: str) -> AsyncGenerator[bool, None]:
    """
    Try to acquire a cluster-wide PostgreSQL advisory lock for the duration of
    the context, so only one replica runs a given background job at a time.

    Usage:
        async with advisory_xact_lock("staking_sync") as is_leader:
            if not is_leader:
                return  # another replica owns this run
            ...  # do the work

    Yields True if this process acquired the lock, False otherwise.

    Implementation notes:
    - Uses a transaction-scoped advisory lock
      (``pg_try_advisory_xact_lock(hashtext(:name))``) held on a dedicated
      connection for the duration of the context.
    - Transaction scope is required on a pooled asyncpg engine: a session-scoped
      advisory lock (``pg_advisory_lock``) can outlive the work and be released
      on — or leak across — a different pooled connection. The xact lock is
      always released when this transaction ends (commit, rollback, or
      connection drop), so a crashed leader never wedges the rest of the fleet.
    - The lock is held on its own connection; the actual job should run on
      separate ``get_db()`` / ``AsyncSessionLocal()`` connections so its own
      commits do not release the lock.
    """
    async with AsyncSessionLocal() as session:
        try:
            result = await session.execute(
                text("SELECT pg_try_advisory_xact_lock(hashtext(:name))"),
                {"name": lock_name},
            )
            yield bool(result.scalar())
        finally:
            # Rollback ends the transaction, releasing the xact-scoped lock.
            await session.rollback()


# FastAPI dependency version (for use with Depends())
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Get a database session for FastAPI dependency injection.
    
    Usage - As FastAPI dependency (request-scoped):
        @router.post("/endpoint")
        async def endpoint(db: AsyncSession = Depends(get_db_session)):
            # Connection held for request duration
            # Automatically committed/rolled back at end of request
    
    Note: This is NOT decorated with @asynccontextmanager because
    FastAPI's Depends() expects a plain async generator, not a context manager.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close() 