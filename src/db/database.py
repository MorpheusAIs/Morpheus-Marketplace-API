from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from typing import AsyncGenerator
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