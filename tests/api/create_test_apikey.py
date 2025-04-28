import asyncio
import logging
from sqlalchemy import select
from datetime import datetime

from src.db.database import AsyncSessionLocal
from src.db.models import User, APIKey
from src.core.security import generate_api_key, get_api_key_hash

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def create_test_api_key():
    """Create a test API key for an existing user."""
    async with AsyncSessionLocal() as db:
        # Get a user
        result = await db.execute(select(User).limit(1))
        user = result.scalars().first()
        
        if not user:
            logger.error("No users found in database")
            return
        
        logger.info(f"Found user: {user.id} - {user.email}")
        
        # Generate API key
        full_key, key_prefix = generate_api_key()
        hashed_key = get_api_key_hash(full_key)
        
        # Create API key
        api_key = APIKey(
            key_prefix=key_prefix,
            hashed_key=hashed_key,
            user_id=user.id,
            name="Test API Key",
            created_at=datetime.utcnow(),
            is_active=True
        )
        
        # Add to database
        db.add(api_key)
        await db.commit()
        await db.refresh(api_key)
        
        logger.info(f"Created API key: {api_key.id} - {api_key.key_prefix}")
        logger.info(f"Full key (save this): {full_key}")

if __name__ == "__main__":
    asyncio.run(create_test_api_key()) 