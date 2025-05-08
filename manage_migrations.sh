#!/bin/bash
# Migration management script for Morpheus API
# This script provides a standardized way to manage database migrations

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Banner
echo -e "${BLUE}==============================${NC}"
echo -e "${BLUE}Morpheus API Migration Manager${NC}"
echo -e "${BLUE}==============================${NC}"

# Function to display usage information
function show_usage {
    echo -e "${YELLOW}Usage:${NC}"
    echo -e "  $0 ${GREEN}create${NC} <description> - Create a new migration"
    echo -e "  $0 ${GREEN}upgrade${NC} - Apply all pending migrations"
    echo -e "  $0 ${GREEN}downgrade${NC} - Downgrade to previous migration"
    echo -e "  $0 ${GREEN}current${NC} - Show current migration version"
    echo -e "  $0 ${GREEN}history${NC} - Show migration history"
    echo -e "  $0 ${GREEN}fix${NC} - Fix alembic versioning (if problems occur)"
    echo -e "  $0 ${GREEN}verify${NC} - Verify migration status"
}

# Check for python3
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Error: python3 is required but not installed${NC}"
    exit 1
fi

# Check for alembic
if ! command -v alembic &> /dev/null; then
    echo -e "${RED}Error: alembic is required but not installed${NC}"
    echo -e "Install with: ${YELLOW}pip install alembic${NC}"
    exit 1
fi

# Process commands
case "$1" in
    create)
        if [ -z "$2" ]; then
            echo -e "${RED}Error: Missing migration description${NC}"
            echo -e "Example: $0 create add_user_table"
            exit 1
        fi
        echo -e "${BLUE}Creating new migration: ${YELLOW}$2${NC}"
        alembic revision --autogenerate -m "$2"
        echo -e "${GREEN}Migration created successfully!${NC}"
        echo -e "Remember to review the generated migration file before applying."
        ;;
    upgrade)
        echo -e "${BLUE}Applying all pending migrations...${NC}"
        alembic upgrade head
        echo -e "${GREEN}Migrations applied successfully!${NC}"
        ;;
    downgrade)
        echo -e "${YELLOW}WARNING: Downgrading database to previous revision${NC}"
        read -p "Are you sure you want to continue? (y/n) " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            alembic downgrade -1
            echo -e "${GREEN}Database downgraded successfully!${NC}"
        else
            echo -e "${BLUE}Downgrade cancelled.${NC}"
        fi
        ;;
    current)
        echo -e "${BLUE}Current database revision:${NC}"
        alembic current
        ;;
    history)
        echo -e "${BLUE}Migration history:${NC}"
        alembic history
        ;;
    fix)
        echo -e "${YELLOW}Attempting to fix alembic versioning...${NC}"
        python3 fix_alembic.py
        echo -e "${GREEN}Attempting to apply migrations...${NC}"
        alembic upgrade head
        ;;
    verify)
        echo -e "${BLUE}Verifying database migration status...${NC}"
        python3 -c "
import asyncio
import os
import sys
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from alembic.script import ScriptDirectory
from alembic.config import Config

# Add the project root to path - use current directory since __file__ is not available
sys.path.insert(0, os.path.abspath('.'))

async def verify_migrations():
    from src.core.config import settings
    
    # Get database URL
    db_url = os.getenv('DATABASE_URL', str(settings.DATABASE_URL))
    print(f'Connecting to database: {db_url}')
    
    # Get expected revision
    config = Config('alembic.ini')
    script = ScriptDirectory.from_config(config)
    head_revision = script.get_current_head()
    print(f'Expected revision (head): {head_revision}')
    
    # Check current revision
    engine = create_async_engine(db_url)
    async with AsyncSession(engine) as session:
        # Check alembic_version table
        result = await session.execute(text(\"SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'alembic_version')\"))
        table_exists = result.scalar()
        
        if not table_exists:
            print('ERROR: alembic_version table does not exist!')
            print('Database has not been initialized. Run: ./manage_migrations.sh upgrade')
            return False
            
        result = await session.execute(text(\"SELECT version_num FROM alembic_version\"))
        current_revision = result.scalar_one_or_none()
        print(f'Current revision: {current_revision}')
        
        if current_revision != head_revision:
            print('ERROR: Database is not up to date!')
            print('Run: ./manage_migrations.sh upgrade')
            return False
            
        # Check sessions table
        result = await session.execute(text(\"SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'sessions')\"))
        sessions_exists = result.scalar()
        
        if not sessions_exists:
            print('ERROR: Sessions table does not exist despite migrations being up to date!')
            print('Run: ./manage_migrations.sh fix')
            return False
            
        print('Database schema is up to date!')
        return True

asyncio.run(verify_migrations())
"
        ;;
    *)
        echo -e "${RED}Error: Unknown command '$1'${NC}"
        show_usage
        exit 1
        ;;
esac 