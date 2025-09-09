#!/bin/bash

# Load environment variables from .env file if it exists
if [ -f .env ]; then
    echo "Loading environment variables from .env file..."
    set -a  # automatically export all variables
    source .env
    set +a  # stop automatically exporting
else
    echo "Warning: .env file not found. Using default values or environment variables."
fi

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration (using environment variables with fallbacks)
ECR_REGISTRY="${ECR_REGISTRY:-${AWS_ACCOUNT_ID:-YOUR_AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION:-us-east-2}.amazonaws.com}"
ECR_REPO="${ECR_REPO:-ecr-morpheus}"
ECS_CLUSTER="${ECS_CLUSTER:-ecs-dev-morpheus-engine}"
ECS_SERVICE="${ECS_SERVICE:-svc-dev-api-service}"
AWS_PROFILE="${AWS_PROFILE:-mor-org-prd}"

echo -e "${BLUE}🚀 Morpheus API Deployment with Local Testing${NC}"
echo "============================================================"

# Function to authenticate with ECR
ecr_login() {
    echo -e "${BLUE}🔐 Authenticating with ECR...${NC}"
    
    # Check if Docker is running
    if ! docker info &> /dev/null; then
        echo -e "${RED}❌ Docker is not running. Please start Docker first.${NC}"
        return 1
    fi
    
    # Just run the damn command directly - no functions, no complexity
    echo -e "${BLUE}Running ECR login directly (no function wrapping)...${NC}"
    
    # Exit the function and run the command in the main script context
    return 2  # Special return code to indicate "run directly"
}

# Function to run local tests
run_local_tests() {
    echo -e "${BLUE}🧪 Running local container tests...${NC}"
    
    # Make the test script executable
    chmod +x test-container-locally.sh
    
    # Run the tests
    ./test-container-locally.sh test
    
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✅ Local tests passed!${NC}"
        return 0
    else
        echo -e "${RED}❌ Local tests failed!${NC}"
        return 1
    fi
}

# Function to run debug tests if main tests fail
run_debug_tests() {
    echo -e "${YELLOW}🐛 Running debug tests to identify issues...${NC}"
    
    echo -e "${BLUE}Testing with minimal app...${NC}"
    ./test-container-locally.sh minimal
    
    if [ $? -eq 0 ]; then
        echo -e "${YELLOW}⚠️ Minimal app works, but full app has issues${NC}"
        echo -e "${BLUE}Running debug startup script...${NC}"
        ./test-container-locally.sh debug
    else
        echo -e "${RED}❌ Even minimal app fails - check Docker build${NC}"
    fi
}

# Function to build and push to ECR
build_and_push() {
    echo -e "${BLUE}🔨 Building and pushing to ECR...${NC}"
    
    # Generate timestamp for tagging
    TIMESTAMP=$(date +%Y%m%d-%H%M%S)
    
    # Build and push
    docker buildx build \
        --no-cache \
        --platform linux/amd64 \
        -t $ECR_REGISTRY/$ECR_REPO:fix-auth-$TIMESTAMP \
        -t $ECR_REGISTRY/$ECR_REPO:dev-latest \
        --push .
    
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✅ Successfully built and pushed to ECR${NC}"
        echo -e "${BLUE}Tags created:${NC}"
        echo -e "  • ${YELLOW}$ECR_REGISTRY/$ECR_REPO:fix-auth-$TIMESTAMP${NC}"
        echo -e "  • ${YELLOW}$ECR_REGISTRY/$ECR_REPO:dev-latest${NC}"
        return 0
    else
        echo -e "${RED}❌ Failed to build and push to ECR${NC}"
        return 1
    fi
}

# Function to deploy to ECS
deploy_to_ecs() {
    echo -e "${BLUE}🚀 Deploying to ECS on cluster ${AWS_REGION}/${ECS_CLUSTER} and service ${ECS_SERVICE} using profile ${AWS_PROFILE}...${NC}"
    
    # Add timeout to prevent hanging like we had with ECR
    echo -e "${YELLOW}Running ECS update command with timeout...${NC}"
    echo -e "${YELLOW}Command: aws ecs update-service --cluster $ECS_CLUSTER --service $ECS_SERVICE --force-new-deployment --profile $AWS_PROFILE --no-paginate${NC}"
    
    local deploy_output
    local exit_code
    
    # Use timeout to prevent hanging - add --no-paginate to prevent interactive paging
    deploy_output=$(timeout 60 aws ecs update-service \
        --cluster $ECS_CLUSTER \
        --service $ECS_SERVICE \
        --force-new-deployment \
        --profile $AWS_PROFILE \
        --no-paginate 2>&1)
    
    exit_code=$?
    
    echo -e "${BLUE}ECS command exit code: $exit_code${NC}"
    echo -e "${BLUE}ECS command output length: ${#deploy_output}${NC}"
    
    if [ $exit_code -eq 124 ]; then
        echo -e "${RED}❌ ECS deployment command timed out after 60 seconds${NC}"
        echo -e "${YELLOW}This suggests the same AWS CLI hanging issue we had with ECR${NC}"
        echo -e "${YELLOW}Please run this command manually:${NC}"
        echo -e "${YELLOW}aws ecs update-service --cluster $ECS_CLUSTER --service $ECS_SERVICE --force-new-deployment --profile $AWS_PROFILE --no-paginate${NC}"
        return 1
    fi
    
    if [ $exit_code -eq 0 ]; then
        echo -e "${GREEN}✅ ECS deployment initiated successfully${NC}"
        
        # Show only essential info from the response
        echo "$deploy_output" | jq -r '.service | "Service: \(.serviceName)\nStatus: \(.status)\nDesired: \(.desiredCount)\nRunning: \(.runningCount)\nPending: \(.pendingCount)"' 2>/dev/null || echo "Deployment initiated"
        
        echo -e "${BLUE}Monitor deployment status:${NC}"
        echo -e "  ${YELLOW}aws ecs describe-services --cluster $ECS_CLUSTER --services $ECS_SERVICE --profile $AWS_PROFILE${NC}"
        return 0
    else
        echo -e "${RED}❌ Failed to deploy to ECS${NC}"
        echo "$deploy_output" | head -10  # Show first 10 lines of error
        return 1
    fi
}

# Function to show deployment status
show_deployment_status() {
    echo -e "${BLUE}📊 Checking ECS deployment status on cluster ${ECS_CLUSTER} and service ${ECS_SERVICE} using profile ${AWS_PROFILE}...${NC}"
    
    # Get concise status info to avoid pagination
    local status_output
    status_output=$(aws ecs describe-services \
        --cluster $ECS_CLUSTER \
        --services $ECS_SERVICE \
        --profile $AWS_PROFILE \
        --query 'services[0].{Status:status,Desired:desiredCount,Running:runningCount,Pending:pendingCount,TaskDef:taskDefinition}' \
        --output json 2>/dev/null)
    
    if [ $? -eq 0 ] && [ -n "$status_output" ]; then
        echo "$status_output" | jq -r '"Status: \(.Status) | Desired: \(.Desired) | Running: \(.Running) | Pending: \(.Pending)"' 2>/dev/null || echo "Status check completed"
        
        # Also show deployment status
        local deploy_status
        deploy_status=$(aws ecs describe-services \
            --cluster $ECS_CLUSTER \
            --services $ECS_SERVICE \
            --profile $AWS_PROFILE \
            --query 'services[0].deployments[0].{Status:status,TaskDef:taskDefinition,CreatedAt:createdAt}' \
            --output json 2>/dev/null)
        
        if [ $? -eq 0 ] && [ -n "$deploy_status" ]; then
            echo "$deploy_status" | jq -r '"Deployment: \(.Status) | Task: \(.TaskDef | split(":") | .[1]) | Created: \(.CreatedAt)"' 2>/dev/null || echo "Deployment info retrieved"
        fi
    else
        echo -e "${RED}❌ Failed to get deployment status${NC}"
    fi
}

# Function to show recent ECS logs
show_ecs_logs() {
    echo -e "${BLUE}📋 Recent ECS logs (last 10 minutes) on cluster ${ECS_CLUSTER} and service ${ECS_SERVICE} using profile ${AWS_PROFILE}...${NC}"
    
    # Get log group name (you may need to adjust this)
    LOG_GROUP="/ecs/morpheus-api"
    
    aws logs filter-log-events \
        --log-group-name $LOG_GROUP \
        --start-time $(date -d '10 minutes ago' +%s)000 \
        --profile $AWS_PROFILE \
        --query 'events[*].[timestamp,message]' \
        --output table 2>/dev/null || echo -e "${YELLOW}⚠️ Could not fetch ECS logs (check log group name)${NC}"
}

# Main deployment function
main_deploy() {
    # Step 0: ECR Authentication (skipped due to hanging issue)
    echo -e "${YELLOW}⚠️ Skipping automatic ECR login due to script hanging issue${NC}"
    echo -e "${YELLOW}Please ensure you're logged into ECR manually before running this script:${NC}"
    echo -e "${YELLOW}aws ecr get-login-password --region us-east-2 --profile mor-org-prd | docker login --username AWS --password-stdin 586794444026.dkr.ecr.us-east-2.amazonaws.com${NC}"
    echo -e "${BLUE}Continuing with deployment on cluster ${ECS_CLUSTER} and service ${ECS_SERVICE} using profile ${AWS_PROFILE}...${NC}"
    
    # Step 1: Run local tests
    if ! run_local_tests; then
        echo -e "${RED}❌ Local tests failed. Deployment aborted.${NC}"
        echo -e "${YELLOW}💡 Try running debug tests: ./deploy-with-testing.sh debug${NC}"
        return 1
    fi
    
    # Step 2: Ask for confirmation
    echo -e "${YELLOW}🤔 Local tests passed. Proceed with deployment? (y/N)${NC}"
    read -r response
    if [[ ! "$response" =~ ^[Yy]$ ]]; then
        echo -e "${BLUE}ℹ️ Deployment cancelled by user${NC}"
        return 0
    fi
    
    # Step 3: Build and push to ECR
    if ! build_and_push; then
        echo -e "${RED}❌ Build and push failed. Deployment aborted.${NC}"
        return 1
    fi
    
    # Step 4: Deploy to ECS
    if ! deploy_to_ecs; then
        echo -e "${RED}❌ ECS deployment failed${NC}"
        return 1
    fi
    
    # Step 5: Wait for deployment to start and show status
    echo -e "${BLUE}⏳ Waiting 60 seconds for deployment to start...${NC}"
    sleep 60
    
    echo -e "${BLUE}📊 Checking deployment progress...${NC}"
    show_deployment_status
    
    # Wait a bit more and check again
    echo -e "${BLUE}⏳ Waiting additional 30 seconds for deployment to progress...${NC}"
    sleep 30
    show_deployment_status
    
    echo -e "${GREEN}🎉 Deployment process completed!${NC}"
    echo -e "${BLUE}📋 Next steps:${NC}"
    echo -e "  1. Monitor deployment: ${YELLOW}./deploy-with-testing.sh status${NC}"
    echo -e "  2. Check logs: ${YELLOW}./deploy-with-testing.sh logs${NC}"
    echo -e "  3. Test production: ${YELLOW}curl https://your-api-domain/health${NC}"
    
    return 0
}

# Parse command line arguments
case "${1:-deploy}" in
    "deploy")
        main_deploy
        ;;
    "test")
        run_local_tests
        ;;
    "debug")
        run_debug_tests
        ;;
    "build")
        build_and_push
        ;;
    "status")
        show_deployment_status
        ;;
    "logs")
        show_ecs_logs
        ;;
    "force")
        echo -e "${YELLOW}⚠️ Force deployment (skipping local tests)${NC}"
        echo -e "${YELLOW}⚠️ Manual ECR login required before force deployment${NC}"
        if build_and_push && deploy_to_ecs; then
            echo -e "${GREEN}✅ Force deployment completed${NC}"
        else
            echo -e "${RED}❌ Force deployment failed${NC}"
        fi
        ;;
    "ecr-test")
        echo -e "${BLUE}🔐 Testing ECR authentication...${NC}"
        echo -e "${YELLOW}⚠️ Skipping automatic ECR login due to hanging issue${NC}"
        echo -e "${YELLOW}Please run this manually before deployment:${NC}"
        echo -e "${YELLOW}aws ecr get-login-password --region us-east-2 --profile mor-org-prd | docker login --username AWS --password-stdin 586794444026.dkr.ecr.us-east-2.amazonaws.com${NC}"
        echo -e "${GREEN}✅ ECR test completed (manual login required)${NC}"
        ;;
    *)
        echo -e "${BLUE}Usage: $0 [deploy|test|debug|build|status|logs|force|ecr-test]${NC}"
        echo -e "  ${YELLOW}deploy${NC} - Run full deployment with local testing (default)"
        echo -e "  ${YELLOW}test${NC}   - Run only local container tests"
        echo -e "  ${YELLOW}debug${NC}  - Run debug tests to identify issues"
        echo -e "  ${YELLOW}build${NC}  - Build and push to ECR only"
        echo -e "  ${YELLOW}status${NC} - Show ECS deployment status"
        echo -e "  ${YELLOW}logs${NC}   - Show recent ECS logs"
        echo -e "  ${YELLOW}force${NC}  - Force deployment without local tests"
        echo -e "  ${YELLOW}ecr-test${NC} - Test ECR authentication only"
        exit 1
        ;;
esac

exit $?
