from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from typing import AsyncGenerator
from contextlib import asynccontextmanager
from src.core.config import settings

# Create async engine instance with explicit pool configuration
# Configured for high-concurrency scenarios (rapid sequential requests)
engine = create_async_engine(
    str(settings.DATABASE_URL), # Ensure URL is a string
    pool_pre_ping=True,
    echo=False, # Set to True for debugging SQL queries
    # Connection pool settings for handling rapid sequential requests
    pool_size=20,              # Base pool size (increased from default 5)
    max_overflow=30,           # Allow burst to 50 total connections
    pool_timeout=30,           # Wait 30s for connection before raising error
    pool_recycle=3600,         # Recycle connections after 1 hour
    pool_reset_on_return='rollback',  # Reset connection state on return
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