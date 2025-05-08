from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from typing import AsyncGenerator
from src.core.config import settings

# Create async engine instance
engine = create_async_engine(
    str(settings.DATABASE_URL), # Ensure URL is a string
    pool_pre_ping=True,
    echo=False, # Set to True for debugging SQL queries
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