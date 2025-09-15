#!/bin/bash

# Local Docker Testing Script for OAuth2
# This builds and runs the container just like ECS would

echo "🐳 Building Morpheus API container..."
docker build -t morpheus-api-test .

echo "🚀 Starting container with .env configuration..."
docker run -d \
  --name morpheus-api-test \
  -p 8000:8000 \
  --env-file .env \
  morpheus-api-test

echo "⏳ Waiting for container to start..."
sleep 5

echo "✅ Container started! Testing OAuth2 configuration..."
echo ""
echo "🌐 Access points:"
echo "  - Swagger UI: http://localhost:8000/docs"
echo "  - Debug endpoint: http://localhost:8000/debug/oauth-config"
echo ""
echo "🔧 Test the OAuth2 modal:"
echo "  1. Open http://localhost:8000/docs"
echo "  2. Click the green 'Authorize' button"
echo "  3. Verify client_id shows: 7faqqo5lcj3175epjqs2upvmmu"
echo ""
echo "📋 Container logs:"
docker logs morpheus-api-test --tail 20

echo ""
echo "🛑 To stop the container:"
echo "  docker stop morpheus-api-test && docker rm morpheus-api-test"
