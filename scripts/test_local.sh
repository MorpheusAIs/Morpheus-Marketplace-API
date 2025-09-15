#!/bin/bash

# Local Testing Script
# Runs the API locally using external dev database and Cognito
# This allows fast cycle testing without full deployment

set -e

echo "🧪 Starting Local Testing Environment"

# Check if .env.local exists
if [ ! -f .env.local ]; then
    echo "❌ .env.local not found!"
    echo "📋 Please copy env.local.example to .env.local and fill in your values:"
    echo "   cp env.local.example .env.local"
    echo "   # Edit .env.local with your dev environment values"
    exit 1
fi

echo "✅ Found .env.local"

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "❌ Docker is not running. Please start Docker and try again."
    exit 1
fi

echo "✅ Docker is running"

# Build and start the local testing environment
echo "🔨 Building and starting local API container..."
docker-compose -f docker-compose.local.yml up --build --remove-orphans

echo "🎉 Local testing environment stopped"
