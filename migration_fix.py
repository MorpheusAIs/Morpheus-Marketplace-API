import asyncio
import logging
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Replace with your actual database URL from .env
DATABASE_URL = "postgresql+asyncpg://morpheus_user:morpheus_password@localhost:5432/morpheus_db"

async def create_table_raw_sql():
    engine = create_async_engine(DATABASE_URL)
    
    # SQL to create the table directly
    create_table_sql = text("""
    CREATE TABLE IF NOT EXISTS user_automation_settings (
        id SERIAL PRIMARY KEY,
        user_id INTEGER UNIQUE REFERENCES users(id) ON DELETE CASCADE,
        is_enabled BOOLEAN DEFAULT false,
        session_duration INTEGER DEFAULT 3600,
        created_at TIMESTAMP DEFAULT NOW(),
        updated_at TIMESTAMP DEFAULT NOW()
    );
    """)
    
    create_index_sql = text("""
    CREATE INDEX IF NOT EXISTS ix_user_automation_settings_id ON user_automation_settings (id);
    """)
    
    async with engine.begin() as conn:
        logger.info("Creating user_automation_settings table")
        try:
            await conn.execute(create_table_sql)
            await conn.execute(create_index_sql)
            logger.info("Table created successfully!")
        except Exception as e:
            logger.error(f"Error creating table: {e}")
    
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(create_table_raw_sql()) 