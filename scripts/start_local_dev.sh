#!/bin/bash

# Local Development Startup Script
# Runs database migrations and starts the API with hot reload

set -e

echo "🧪 Starting Local Development Environment"

# Wait for database to be ready
echo "⏳ Waiting for database to be ready..."
while ! pg_isready -h db-local -p 5432 -U morpheus_local; do
    echo "   Database not ready, waiting 2s..."
    sleep 2
done
echo "✅ Database is ready"

# Run database migrations
echo "🗄️ Running database migrations..."
alembic upgrade head
echo "✅ Database migrations completed"

# Check if we have all required tables
echo "🔍 Verifying database structure..."
python -c "
import asyncio
import sys
sys.path.insert(0, '/app')

async def verify_tables():
    try:
        from src.db.database import engine
        from sqlalchemy import text
        
        async with engine.begin() as conn:
            # Check for key tables
            result = await conn.execute(text(
                \"SELECT table_name FROM information_schema.tables WHERE table_schema='public'\"
            ))
            tables = [row[0] for row in result.fetchall()]
            
            required_tables = ['users', 'api_keys', 'chats', 'messages', 'sessions']
            missing_tables = [t for t in required_tables if t not in tables]
            
            if missing_tables:
                print(f'❌ Missing tables: {missing_tables}')
                sys.exit(1)
            else:
                print('✅ All required tables present')
                
        await engine.dispose()
    except Exception as e:
        print(f'❌ Database verification failed: {e}')
        sys.exit(1)

asyncio.run(verify_tables())
"

echo "🚀 Starting API with hot reload..."
exec uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
