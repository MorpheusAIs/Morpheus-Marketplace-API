#!/bin/bash

# Quick rebuild and test script for OAuth2 changes
echo "🔄 Stopping existing container..."
docker stop morpheus-api-test 2>/dev/null
docker rm morpheus-api-test 2>/dev/null

echo "🏗️ Rebuilding container..."
docker build -t morpheus-api-test .

echo "🚀 Starting with clean .env config..."
docker run -d \
  --name morpheus-api-test \
  -p 8000:8000 \
  --env-file .env.docker-test \
  morpheus-api-test

echo "⏳ Waiting for startup..."
sleep 3

echo "✅ Ready! Test at: http://localhost:8000/docs"
echo "🔍 Debug: http://localhost:8000/debug/oauth-config"
