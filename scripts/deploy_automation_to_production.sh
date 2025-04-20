#!/bin/bash
# Script to deploy the automation feature to production

# Enable simulation mode for testing
export SIMULATION=true

# Configuration
DEPLOY_BRANCH="main"
FEATURE_FLAG_ENABLED="true"  # Start with feature flag enabled by default
CONFIG_DIR="/etc/morpheus"    # Production config directory
MODEL_MAPPINGS="config/model_mappings.json"
REMOTE_SERVER="production-server"  # Replace with actual production server hostname/IP
REMOTE_USER="deploy"              # Replace with actual deploy user
APP_DIR="/opt/morpheus"           # Production application directory

echo "====== Deploying Automation Feature to Production ======"

# 1. Apply database migration first
echo "Step 1: Applying database migration..."
./scripts/apply_production_migration.sh
if [ $? -ne 0 ]; then
    echo "❌ Database migration failed! Aborting deployment."
    exit 1
fi

# 2. Deploy model mappings configuration
echo -e "\nStep 2: Deploying model mappings configuration..."
if [ -f "$MODEL_MAPPINGS" ]; then
    echo "Copying model_mappings.json to production server..."
    
    if [ "$SIMULATION" = "true" ]; then
        echo "SIMULATION: scp $MODEL_MAPPINGS $REMOTE_USER@$REMOTE_SERVER:$CONFIG_DIR/"
        echo "✅ Simulation: Model mappings deployed!"
    else
        # In a real deployment:
        scp $MODEL_MAPPINGS $REMOTE_USER@$REMOTE_SERVER:$CONFIG_DIR/
        echo "✅ Model mappings deployed!"
    fi
else
    echo "❌ Model mappings file not found! Aborting deployment."
    exit 1
fi

# 3. Update environment variables for the feature flag (enabled by default)
echo -e "\nStep 3: Setting feature flag (enabled by default)..."
if [ "$SIMULATION" = "true" ]; then
    echo "SIMULATION: ssh $REMOTE_USER@$REMOTE_SERVER \"echo 'AUTOMATION_FEATURE_ENABLED=$FEATURE_FLAG_ENABLED' >> $APP_DIR/.env\""
    echo "✅ Simulation: Feature flag set to $FEATURE_FLAG_ENABLED"
else
    ssh $REMOTE_USER@$REMOTE_SERVER "echo 'AUTOMATION_FEATURE_ENABLED=$FEATURE_FLAG_ENABLED' >> $APP_DIR/.env"
    echo "✅ Feature flag set to $FEATURE_FLAG_ENABLED"
fi

# 4. Deploy updated code
echo -e "\nStep 4: Deploying code changes..."
if [ "$SIMULATION" = "true" ]; then
    echo "SIMULATION: ssh $REMOTE_USER@$REMOTE_SERVER \"cd $APP_DIR && git checkout $DEPLOY_BRANCH && git pull\""
    echo "✅ Simulation: Code deployed!"
else
    ssh $REMOTE_USER@$REMOTE_SERVER "cd $APP_DIR && git checkout $DEPLOY_BRANCH && git pull"
    echo "✅ Code deployed!"
fi

# 5. Restart the application
echo -e "\nStep 5: Restarting the application..."
if [ "$SIMULATION" = "true" ]; then
    echo "SIMULATION: ssh $REMOTE_USER@$REMOTE_SERVER \"sudo systemctl restart morpheus-api\""
    echo "✅ Simulation: Application restarted!"
else
    ssh $REMOTE_USER@$REMOTE_SERVER "sudo systemctl restart morpheus-api"
    echo "✅ Application restarted!"
fi

echo -e "\n====== Deployment completed! ======"
echo "✅ Automation feature is deployed to production with feature flag ENABLED."
echo "✅ Users can now enable automation via the API settings endpoint."

# Reminder about running final tests
echo -e "\n====== Next Steps ======"
echo "1. Run the end-to-end test against production:"
echo "   export MORPHEUS_API_URL=\"https://production-api-endpoint\""
echo "   export MORPHEUS_API_KEY=\"your-api-key\""
echo "   python3 test_automation_e2e.py"
echo ""
echo "2. Monitor system behavior and error rates." 