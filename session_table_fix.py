import asyncio
import logging
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Database URL from .env
DATABASE_URL = "postgresql+asyncpg://morpheus_user:morpheus_password@localhost:5432/morpheus_db"

async def create_user_sessions_table():
    engine = create_async_engine(DATABASE_URL)
    
    # SQL to create the user_sessions table
    create_table_sql = text("""
    CREATE TABLE IF NOT EXISTS user_sessions (
        id SERIAL PRIMARY KEY,
        api_key_id INTEGER REFERENCES api_keys(id) ON DELETE CASCADE,
        session_id VARCHAR NOT NULL,
        model_id VARCHAR,
        created_at TIMESTAMP DEFAULT NOW(),
        expires_at TIMESTAMP,
        is_active BOOLEAN DEFAULT TRUE
    );
    """)
    
    # Create index for api_key_id
    create_id_index_sql = text("""
    CREATE INDEX IF NOT EXISTS ix_user_sessions_id ON user_sessions (id);
    """)
    
    # Create index for session_id
    create_session_index_sql = text("""
    CREATE INDEX IF NOT EXISTS ix_user_sessions_session_id ON user_sessions (session_id);
    """)
    
    # Create unique index for active sessions
    create_unique_index_sql = text("""
    CREATE UNIQUE INDEX IF NOT EXISTS unique_active_api_key_session ON user_sessions (api_key_id) WHERE is_active = true;
    """)
    
    async with engine.begin() as conn:
        logger.info("Creating user_sessions table")
        try:
            await conn.execute(create_table_sql)
            await conn.execute(create_id_index_sql)
            await conn.execute(create_session_index_sql)
            await conn.execute(create_unique_index_sql)
            logger.info("Table created successfully!")
        except Exception as e:
            logger.error(f"Error creating table: {e}")
    
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(create_user_sessions_table()) 