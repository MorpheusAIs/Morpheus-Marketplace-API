#!/usr/bin/env python
"""
Script to fix Alembic versioning issues.
This script directly updates the alembic_version table to match the latest revision.
"""
import os
import sys
import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

# Add the project root directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

# Import settings from your project
from src.core.config import settings

# The correct revision ID from your latest migration
CORRECT_REVISION = '881e615d25ac'

async def fix_alembic_version():
    """Fix the alembic_version table to use the correct revision."""
    # Get database URL from environment or settings
    db_url = os.getenv("DATABASE_URL", str(settings.DATABASE_URL))
    if not db_url:
        print("ERROR: DATABASE_URL environment variable or setting must be set")
        sys.exit(1)
    
    print(f"Connecting to database: {db_url}")
    engine = create_async_engine(db_url)
    
    async with AsyncSession(engine) as session:
        try:
            # Check if alembic_version table exists
            result = await session.execute(text(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'alembic_version')"
            ))
            table_exists = result.scalar()
            
            if not table_exists:
                print("Creating alembic_version table...")
                await session.execute(text(
                    "CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"
                ))
                await session.execute(text(
                    f"INSERT INTO alembic_version VALUES ('{CORRECT_REVISION}')"
                ))
            else:
                # Check current version
                result = await session.execute(text("SELECT version_num FROM alembic_version"))
                current_version = result.scalar_one_or_none()
                
                print(f"Current alembic version: {current_version}")
                print(f"Target version: {CORRECT_REVISION}")
                
                if current_version != CORRECT_REVISION:
                    print("Updating alembic_version table...")
                    if current_version:
                        await session.execute(text(
                            f"UPDATE alembic_version SET version_num = '{CORRECT_REVISION}'"
                        ))
                    else:
                        await session.execute(text(
                            f"INSERT INTO alembic_version VALUES ('{CORRECT_REVISION}')"
                        ))
                else:
                    print("Alembic version is already correct.")
            
            await session.commit()
            print("Alembic version fixed successfully!")
            
        except Exception as e:
            print(f"ERROR: {e}")
            await session.rollback()
            sys.exit(1)

if __name__ == "__main__":
    asyncio.run(fix_alembic_version()) 