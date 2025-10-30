from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from typing import AsyncGenerator
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

# Dependency to get DB session
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close() 