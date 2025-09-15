#!/usr/bin/env python3
"""
Script to create database tables directly, bypassing Alembic.
Use this only if regular migrations are not working.
"""
import asyncio
import os
import sys
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

# Add project root to path
sys.path.insert(0, os.path.abspath('.'))

# Import settings from your project
from src.core.config import settings

async def create_tables():
    """Create database tables directly."""
    # Get database URL from environment or settings
    db_url = os.getenv("DATABASE_URL", str(settings.DATABASE_URL))
    if not db_url:
        print("ERROR: DATABASE_URL environment variable or setting must be set")
        sys.exit(1)
    
    print(f"Connecting to database: {db_url}")
    engine = create_async_engine(db_url)
    
    async with AsyncSession(engine) as session:
        try:
            # Check if sessions table exists
            result = await session.execute(text(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'sessions')"
            ))
            sessions_exists = result.scalar()
            
            if sessions_exists:
                print("Sessions table already exists, skipping creation.")
            else:
                print("Creating sessions table...")
                # SQL copied directly from the migration
                await session.execute(text("""
                CREATE TABLE sessions (
                    id VARCHAR NOT NULL, 
                    user_id INTEGER, 
                    api_key_id INTEGER, 
                    model VARCHAR NOT NULL, 
                    type VARCHAR NOT NULL, 
                    created_at TIMESTAMP WITHOUT TIME ZONE, 
                    expires_at TIMESTAMP WITHOUT TIME ZONE NOT NULL, 
                    is_active BOOLEAN, 
                    PRIMARY KEY (id), 
                    FOREIGN KEY(api_key_id) REFERENCES api_keys (id), 
                    FOREIGN KEY(user_id) REFERENCES users (id)
                )
                """))
                
                await session.execute(text(
                    "CREATE INDEX ix_sessions_api_key_id ON sessions (api_key_id)"
                ))
                
                await session.execute(text(
                    "CREATE INDEX ix_sessions_is_active ON sessions (is_active)"
                ))
                
                await session.execute(text("""
                CREATE UNIQUE INDEX sessions_active_api_key_unique 
                ON sessions (api_key_id, is_active)
                WHERE is_active IS true
                """))
                
                print("Successfully created sessions table")
            
            # Check if delegations table exists
            result = await session.execute(text(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'delegations')"
            ))
            delegations_exists = result.scalar()
            
            if delegations_exists:
                print("Delegations table already exists, skipping creation.")
            else:
                print("Creating delegations table...")
                # SQL copied directly from the migration
                await session.execute(text("""
                CREATE TABLE delegations (
                    id SERIAL NOT NULL, 
                    user_id INTEGER NOT NULL, 
                    delegate_address VARCHAR NOT NULL, 
                    signed_delegation_data TEXT NOT NULL, 
                    expiry TIMESTAMP WITHOUT TIME ZONE, 
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(), 
                    is_active BOOLEAN, 
                    PRIMARY KEY (id), 
                    FOREIGN KEY(user_id) REFERENCES users (id)
                )
                """))
                
                await session.execute(text(
                    "CREATE INDEX ix_delegations_delegate_address ON delegations (delegate_address)"
                ))
                
                await session.execute(text(
                    "CREATE INDEX ix_delegations_id ON delegations (id)"
                ))
                
                await session.execute(text(
                    "CREATE INDEX ix_delegations_is_active ON delegations (is_active)"
                ))
                
                await session.execute(text(
                    "CREATE INDEX ix_delegations_user_id ON delegations (user_id)"
                ))
                
                print("Successfully created delegations table")
                
            # Commit changes
            await session.commit()
            print("All tables created successfully!")
            
        except Exception as e:
            print(f"ERROR: {e}")
            await session.rollback()
            sys.exit(1)

if __name__ == "__main__":
    asyncio.run(create_tables()) 