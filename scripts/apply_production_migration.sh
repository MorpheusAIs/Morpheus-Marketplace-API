#!/bin/bash
# Script to apply the automation settings migration to production database

# Simulation flag - set to true for simulated deployment
SIMULATION=${SIMULATION:-true}

if [ "$SIMULATION" = "true" ]; then
    echo "====== SIMULATION MODE: Migration to Production ======"
    echo "In simulation mode, commands will be displayed but not executed."
    echo ""
    
    echo "COMMAND: Apply SQL migration to production database"
    echo "SQL:"
    cat migration/create_automation_settings.sql
    echo ""
    echo "✅ Simulation: Migration applied successfully!"
    echo "✅ Simulation: Table 'user_automation_settings' exists!"
    exit 0
fi

# Production database connection details (used in real deployment)
PROD_DB_HOST=${PROD_DB_HOST:-"production-db-hostname"}
PROD_DB_PORT=${PROD_DB_PORT:-5432}
PROD_DB_NAME=${PROD_DB_NAME:-"morpheus_db"}
PROD_DB_USER=${PROD_DB_USER:-"postgres"}
PROD_DB_PASSWORD=${PROD_DB_PASSWORD:-"postgres_password"}

echo "====== Applying Automation Settings Migration to Production ======"
echo "Using database: $PROD_DB_HOST:$PROD_DB_PORT/$PROD_DB_NAME"
echo ""

# Export password for psql
export PGPASSWORD=$PROD_DB_PASSWORD

# Check if the migration file exists
if [ ! -f "migration/create_automation_settings.sql" ]; then
    echo "Error: Migration file not found!"
    exit 1
fi

echo "Applying migration..."
# Apply the SQL migration
if command -v psql &> /dev/null; then
    # If psql is available locally
    psql -h $PROD_DB_HOST -p $PROD_DB_PORT -U $PROD_DB_USER -d $PROD_DB_NAME -f migration/create_automation_settings.sql
elif command -v docker &> /dev/null; then
    # If docker is available
    cat migration/create_automation_settings.sql | docker run --rm -i --network=host \
        -e PGPASSWORD=$PROD_DB_PASSWORD \
        postgres:15-alpine psql -h $PROD_DB_HOST -p $PROD_DB_PORT -U $PROD_DB_USER -d $PROD_DB_NAME
else
    echo "Error: Neither psql nor docker is available. Cannot apply migration."
    exit 1
fi

# Check if the migration was successful
if [ $? -eq 0 ]; then
    echo "✅ Migration applied successfully!"
else
    echo "❌ Migration failed!"
    exit 1
fi

echo ""
echo "Verifying table existence..."
# Verify the table was created
if command -v psql &> /dev/null; then
    # If psql is available locally
    TABLE_EXISTS=$(psql -h $PROD_DB_HOST -p $PROD_DB_PORT -U $PROD_DB_USER -d $PROD_DB_NAME -t -c "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'user_automation_settings');")
elif command -v docker &> /dev/null; then
    # If docker is available
    TABLE_EXISTS=$(echo "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'user_automation_settings');" | docker run --rm -i --network=host \
        -e PGPASSWORD=$PROD_DB_PASSWORD \
        postgres:15-alpine psql -h $PROD_DB_HOST -p $PROD_DB_PORT -U $PROD_DB_USER -d $PROD_DB_NAME -t)
fi

if [[ $TABLE_EXISTS == *"t"* ]]; then
    echo "✅ Table 'user_automation_settings' exists!"
else
    echo "❌ Table 'user_automation_settings' does not exist!"
    exit 1
fi

echo ""
echo "====== Migration completed successfully! ======" 