#!/bin/bash

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
LOCAL_IMAGE_NAME="morpheus-api-local-test"
CONTAINER_NAME="morpheus-api-test"
TEST_PORT="8001"  # Use different port to avoid conflicts

echo -e "${BLUE}🚀 Morpheus API Local Container Testing${NC}"
echo "=================================================="

# Function to cleanup previous test containers
cleanup() {
    echo -e "${YELLOW}🧹 Cleaning up previous test containers...${NC}"
    docker stop $CONTAINER_NAME 2>/dev/null || true
    docker rm $CONTAINER_NAME 2>/dev/null || true
    docker rmi $LOCAL_IMAGE_NAME 2>/dev/null || true
}

# Function to build the container locally
build_container() {
    echo -e "${BLUE}🔨 Building container locally...${NC}"
    
    # Try safe Dockerfile first
    if [ -f "Dockerfile.safe" ]; then
        echo -e "${YELLOW}Using safe Dockerfile for testing...${NC}"
        docker build --no-cache -f Dockerfile.safe -t $LOCAL_IMAGE_NAME .
    else
        echo -e "${YELLOW}Using standard Dockerfile...${NC}"
        docker build --no-cache -t $LOCAL_IMAGE_NAME .
    fi
    
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✅ Container built successfully${NC}"
        return 0
    else
        echo -e "${RED}❌ Container build failed${NC}"
        return 1
    fi
}

# Function to test basic container startup
test_basic_startup() {
    echo -e "${BLUE}🧪 Testing basic container startup...${NC}"
    
    # Start container with minimal configuration
    docker run -d \
        --name $CONTAINER_NAME \
        -p $TEST_PORT:8000 \
        -e DATABASE_URL="postgresql+asyncpg://test:test@localhost:5432/test" \
        -e JWT_SECRET_KEY="test-secret-key-for-local-testing" \
        -e COGNITO_USER_POOL_ID="us-east-2_test" \
        -e COGNITO_CLIENT_ID="test-client-id" \
        -e COGNITO_REGION="us-east-2" \
        -e COGNITO_DOMAIN="test.auth.com" \
        $LOCAL_IMAGE_NAME
    
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✅ Container started successfully${NC}"
        return 0
    else
        echo -e "${RED}❌ Container failed to start${NC}"
        return 1
    fi
}

# Function to wait for container to be ready
wait_for_container() {
    echo -e "${BLUE}⏳ Waiting for container to be ready...${NC}"
    
    # Wait up to 60 seconds for the container to be ready
    for i in {1..60}; do
        if curl -s http://localhost:$TEST_PORT/health > /dev/null 2>&1; then
            echo -e "${GREEN}✅ Container is ready!${NC}"
            return 0
        fi
        
        # Check if container is still running
        if ! docker ps | grep -q $CONTAINER_NAME; then
            echo -e "${RED}❌ Container stopped unexpectedly${NC}"
            echo -e "${YELLOW}📋 Container logs:${NC}"
            docker logs $CONTAINER_NAME
            return 1
        fi
        
        echo -n "."
        sleep 1
    done
    
    echo -e "${RED}❌ Container failed to become ready within 60 seconds${NC}"
    return 1
}

# Function to test endpoints
test_endpoints() {
    echo -e "${BLUE}🔍 Testing API endpoints...${NC}"
    
    # Test health endpoint
    echo -e "${YELLOW}Testing /health endpoint...${NC}"
    health_response=$(curl -s http://localhost:$TEST_PORT/health)
    if echo "$health_response" | grep -q "healthy"; then
        echo -e "${GREEN}✅ Health endpoint working${NC}"
    else
        echo -e "${RED}❌ Health endpoint failed${NC}"
        echo "Response: $health_response"
        return 1
    fi
    
    # Test root endpoint
    echo -e "${YELLOW}Testing / endpoint...${NC}"
    root_response=$(curl -s http://localhost:$TEST_PORT/)
    if echo "$root_response" | grep -q "Morpheus API Gateway"; then
        echo -e "${GREEN}✅ Root endpoint working${NC}"
    else
        echo -e "${RED}❌ Root endpoint failed${NC}"
        echo "Response: $root_response"
        return 1
    fi
    
    # Test docs endpoint
    echo -e "${YELLOW}Testing /docs endpoint...${NC}"
    docs_status=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:$TEST_PORT/docs)
    if [ "$docs_status" = "200" ]; then
        echo -e "${GREEN}✅ Docs endpoint working${NC}"
    else
        echo -e "${RED}❌ Docs endpoint failed (HTTP $docs_status)${NC}"
        return 1
    fi
    
    # Test new auth demo endpoint
    echo -e "${YELLOW}Testing /auth-demo endpoint...${NC}"
    demo_status=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:$TEST_PORT/auth-demo)
    if [ "$demo_status" = "200" ]; then
        echo -e "${GREEN}✅ Auth demo endpoint working${NC}"
    else
        echo -e "${RED}❌ Auth demo endpoint failed (HTTP $demo_status)${NC}"
        return 1
    fi
    
    # Test Cognito config endpoint
    echo -e "${YELLOW}Testing /api/v1/auth/cognito/config endpoint...${NC}"
    config_response=$(curl -s http://localhost:$TEST_PORT/api/v1/auth/cognito/config)
    if echo "$config_response" | grep -q "client_id"; then
        echo -e "${GREEN}✅ Cognito config endpoint working${NC}"
    else
        echo -e "${RED}❌ Cognito config endpoint failed${NC}"
        echo "Response: $config_response"
        return 1
    fi
    
    return 0
}

# Function to show container logs
show_logs() {
    echo -e "${BLUE}📋 Container logs:${NC}"
    docker logs $CONTAINER_NAME
}

# Function to show container stats
show_stats() {
    echo -e "${BLUE}📊 Container stats:${NC}"
    docker stats $CONTAINER_NAME --no-stream
}

# Main testing function
run_tests() {
    cleanup
    
    if ! build_container; then
        return 1
    fi
    
    if ! test_basic_startup; then
        return 1
    fi
    
    if ! wait_for_container; then
        show_logs
        return 1
    fi
    
    show_stats
    
    if ! test_endpoints; then
        show_logs
        return 1
    fi
    
    echo -e "${GREEN}🎉 All tests passed! Container is working correctly.${NC}"
    echo -e "${BLUE}📱 You can test the API at:${NC}"
    echo -e "  • Health: ${YELLOW}http://localhost:$TEST_PORT/health${NC}"
    echo -e "  • API Docs: ${YELLOW}http://localhost:$TEST_PORT/docs${NC}"
    echo -e "  • Auth Demo: ${YELLOW}http://localhost:$TEST_PORT/auth-demo${NC}"
    echo -e "  • Root: ${YELLOW}http://localhost:$TEST_PORT/${NC}"
    
    return 0
}

# Function to run debug mode
run_debug() {
    echo -e "${BLUE}🐛 Running in debug mode...${NC}"
    cleanup
    
    if ! build_container; then
        return 1
    fi
    
    echo -e "${YELLOW}Starting container with debug startup script...${NC}"
    docker run -it --rm \
        --name $CONTAINER_NAME \
        -p $TEST_PORT:8000 \
        -e DATABASE_URL="postgresql+asyncpg://test:test@localhost:5432/test" \
        -e JWT_SECRET_KEY="test-secret-key-for-local-testing" \
        -e COGNITO_USER_POOL_ID="us-east-2_test" \
        -e COGNITO_CLIENT_ID="test-client-id" \
        -e COGNITO_REGION="us-east-2" \
        -e COGNITO_DOMAIN="test.auth.com" \
        $LOCAL_IMAGE_NAME \
        python debug_startup.py
}

# Function to run with minimal app
run_minimal() {
    echo -e "${BLUE}🔬 Running with minimal app...${NC}"
    cleanup
    
    if ! build_container; then
        return 1
    fi
    
    echo -e "${YELLOW}Starting container with minimal app...${NC}"
    docker run -d \
        --name $CONTAINER_NAME \
        -p $TEST_PORT:8000 \
        -e DATABASE_URL="postgresql+asyncpg://test:test@localhost:5432/test" \
        -e JWT_SECRET_KEY="test-secret-key-for-local-testing" \
        $LOCAL_IMAGE_NAME \
        gunicorn -k uvicorn.workers.UvicornWorker -w 1 -b 0.0.0.0:8000 src.main_minimal:app
    
    if wait_for_container; then
        echo -e "${GREEN}✅ Minimal app is working!${NC}"
        echo -e "${BLUE}Test endpoints:${NC}"
        echo -e "  • ${YELLOW}http://localhost:$TEST_PORT/${NC}"
        echo -e "  • ${YELLOW}http://localhost:$TEST_PORT/debug/env${NC}"
        echo -e "  • ${YELLOW}http://localhost:$TEST_PORT/debug/imports${NC}"
    else
        show_logs
        return 1
    fi
}

# Parse command line arguments
case "${1:-test}" in
    "test")
        run_tests
        ;;
    "debug")
        run_debug
        ;;
    "minimal")
        run_minimal
        ;;
    "cleanup")
        cleanup
        echo -e "${GREEN}✅ Cleanup completed${NC}"
        ;;
    "logs")
        show_logs
        ;;
    *)
        echo -e "${BLUE}Usage: $0 [test|debug|minimal|cleanup|logs]${NC}"
        echo -e "  ${YELLOW}test${NC}    - Run full container tests (default)"
        echo -e "  ${YELLOW}debug${NC}   - Run debug startup script"
        echo -e "  ${YELLOW}minimal${NC} - Run with minimal app"
        echo -e "  ${YELLOW}cleanup${NC} - Clean up test containers"
        echo -e "  ${YELLOW}logs${NC}    - Show container logs"
        exit 1
        ;;
esac

exit_code=$?

# Always cleanup unless we're showing logs or running minimal/debug interactively
if [ "$1" != "logs" ] && [ "$1" != "minimal" ] && [ "$1" != "debug" ]; then
    echo -e "${YELLOW}🧹 Cleaning up...${NC}"
    cleanup
fi

exit $exit_code
