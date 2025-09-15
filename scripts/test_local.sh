#!/bin/bash

# Self-Contained Local Testing Script
# Runs the API with local PostgreSQL and bypassed authentication
# Perfect for development without external dependencies

set -e

echo "🧪 Starting Self-Contained Local Testing Environment"

# Check if .env.local exists
if [ ! -f .env.local ]; then
    echo "❌ .env.local not found!"
    echo "📋 Please copy env.local.example to .env.local:"
    echo "   cp env.local.example .env.local"
    echo "   # Edit .env.local if needed (defaults should work)"
    exit 1
fi

echo "✅ Found .env.local"

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker is not running. Please start Docker and try again."
    exit 1
fi

echo "✅ Docker is running"

# Clean up any previous containers
echo "🧹 Cleaning up previous containers..."
docker compose -f docker-compose.local.yml down --volumes --remove-orphans

# Build and start the self-contained environment
echo "🔨 Building and starting self-contained local environment..."
echo "📦 This includes:"
echo "   - Local PostgreSQL database (ephemeral)"
echo "   - API with bypassed Cognito authentication"
echo "   - Hot reload for development"
echo "   - Test user: test@local.dev"

docker compose -f docker-compose.local.yml up --build --remove-orphans

echo "🎉 Self-contained local testing environment stopped"
