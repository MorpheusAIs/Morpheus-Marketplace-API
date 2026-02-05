#!/bin/bash

# Local Docker Testing Script with Cognito Authentication
# This uses docker-compose.local.yml for proper environment variable injection

echo "🐳 Building and starting Morpheus API with docker-compose..."

# Check if .env.local exists
if [ ! -f .env.local ]; then
    echo "❌ .env.local not found!"
    echo "📋 Please copy env.local.example to .env.local:"
    echo "   cp env.local.example .env.local"
    echo "   # Edit .env.local with your Cognito settings if needed"
    exit 1
fi

echo "✅ Found .env.local"

# Stop and remove any existing containers
if [ "$1" == "clean" ]; then
    echo "🧹 Cleaning up existing containers..."
    docker compose -f docker-compose.local.yml down --remove-orphans
elif [ "$1" == "db-clean-volumes" ]; then
    echo "🧹 Cleaning up existing containers and database volumes..."
    docker compose -f docker-compose.local.yml down --volumes --remove-orphans
else
    echo "🧹 Skipping cleanup"
    docker compose -f docker-compose.local.yml down
fi

# Build and start services
echo "🚀 Starting services with docker-compose..."
docker compose -f docker-compose.local.yml up --build

echo "⏳ Waiting for container to start..."
sleep 5

echo "✅ Container started! Testing OAuth2 configuration..."
echo ""
echo "🌐 Access points:"
echo "  - Swagger UI: http://localhost:8000/docs"
echo "  - Debug endpoint: http://localhost:8000/debug/oauth-config"
echo ""
echo "🔧 Test the Cognito OAuth2 authentication:"
echo "  1. Open http://localhost:8000/docs"
echo "  2. Click the green 'Authorize' button"
echo "  3. You should be redirected to Cognito login (auth.mor.org)"
echo "  4. Login with your dev environment credentials"
echo ""
echo "📋 API Container logs:"
docker compose -f docker-compose.local.yml logs api-local --tail 20

echo ""
echo "🛑 To stop all services:"
echo "  docker compose -f docker-compose.local.yml down"
echo ""
echo "🔍 To view all service logs:"
echo "  docker compose -f docker-compose.local.yml logs -f"
