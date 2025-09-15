#!/bin/bash
# Quick health check script for Direct Model Service
# Usage: ./scripts/quick_health_check.sh [API_URL]

set -e

# Default to dev environment
API_URL=${1:-"https://api.dev.mor.org"}

echo "🔍 Quick Health Check for Direct Model Service"
echo "================================================"
echo "API URL: $API_URL"
echo ""

# Function to check HTTP status and extract key info
check_endpoint() {
    local url=$1
    local description=$2
    
    echo "🔍 Checking $description..."
    
    # Make request and capture both status and response
    response=$(curl -s -w "HTTPSTATUS:%{http_code}" "$url" 2>/dev/null || echo "HTTPSTATUS:000")
    
    # Extract HTTP status code
    http_code=$(echo "$response" | grep -o "HTTPSTATUS:[0-9]*" | cut -d: -f2)
    
    # Extract response body
    body=$(echo "$response" | sed -E 's/HTTPSTATUS:[0-9]*$//')
    
    if [ "$http_code" -eq 200 ]; then
        echo "  ✅ HTTP $http_code - OK"
        return 0
    else
        echo "  ❌ HTTP $http_code - FAILED"
        if [ -n "$body" ]; then
            echo "     Response: $(echo "$body" | head -c 200)..."
        fi
        return 1
    fi
}

# Function to extract and display key metrics
extract_metrics() {
    local url=$1
    local jq_filter=$2
    local description=$3
    
    response=$(curl -s "$url" 2>/dev/null || echo "{}")
    
    if [ "$response" != "{}" ]; then
        value=$(echo "$response" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    result = data
    for key in '$jq_filter'.split('.'):
        if key and key != 'data':
            result = result.get(key, {})
    print(result if isinstance(result, (str, int, float)) else 'N/A')
except:
    print('N/A')
")
        echo "     $description: $value"
    fi
}

echo "1. Basic Health Check"
echo "---------------------"
if check_endpoint "$API_URL/health" "Main health endpoint"; then
    extract_metrics "$API_URL/health" "model_service.status" "Model Service Status"
    extract_metrics "$API_URL/health" "model_service.model_count" "Model Count"
    extract_metrics "$API_URL/health" "model_service.cache_info.cached_models" "Cached Models"
fi
echo ""

echo "2. Detailed Model Health"
echo "------------------------"
if check_endpoint "$API_URL/health/models" "Model health endpoint"; then
    extract_metrics "$API_URL/health/models" "status" "Service Status"
    extract_metrics "$API_URL/health/models" "model_counts.total_models" "Total Models"
    extract_metrics "$API_URL/health/models" "cache_stats.cached_models" "Cached Models"
    extract_metrics "$API_URL/health/models" "cache_stats.seconds_until_expiry" "Cache TTL (seconds)"
fi
echo ""

echo "3. Models List Endpoint"
echo "-----------------------"
if check_endpoint "$API_URL/api/v1/models" "Models list endpoint"; then
    # Count models in response
    model_count=$(curl -s "$API_URL/api/v1/models" 2>/dev/null | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    models = data.get('data', [])
    print(len(models))
except:
    print('N/A')
")
    echo "     Available Models: $model_count"
    
    # Show first few model names
    sample_models=$(curl -s "$API_URL/api/v1/models" 2>/dev/null | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    models = data.get('data', [])
    names_with_types = [f\"{m.get('id', 'unknown')} ({m.get('modelType', 'UNKNOWN')})\" for m in models[:5]]
    print(', '.join(names_with_types))
except:
    print('N/A')
")
    echo "     Sample Models: $sample_models"
fi
echo ""

echo "4. Model Resolution Test"
echo "------------------------"
echo "🧪 Testing venice-uncensored resolution..."

# Test model resolution via chat completions (expect auth error)
response=$(curl -s -w "HTTPSTATUS:%{http_code}" -X POST "$API_URL/api/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -d '{"model": "venice-uncensored", "messages": [{"role": "user", "content": "test"}], "max_tokens": 1}' \
    2>/dev/null || echo "HTTPSTATUS:000")

http_code=$(echo "$response" | grep -o "HTTPSTATUS:[0-9]*" | cut -d: -f2)

if [ "$http_code" -eq 401 ] || [ "$http_code" -eq 403 ]; then
    echo "  ✅ Model resolved successfully (auth error expected: HTTP $http_code)"
elif [ "$http_code" -eq 400 ] || [ "$http_code" -eq 422 ]; then
    echo "  ❌ Model resolution failed (HTTP $http_code)"
    body=$(echo "$response" | sed -E 's/HTTPSTATUS:[0-9]*$//')
    echo "     Error: $(echo "$body" | head -c 200)..."
else
    echo "  ⚠️  Unexpected response (HTTP $http_code)"
fi
echo ""

echo "5. Environment Configuration"
echo "----------------------------"
if [ "$API_URL" = "https://api.dev.mor.org" ]; then
    expected_source="https://active.dev.mor.org/active_models.json"
    echo "  Environment: Development"
elif [ "$API_URL" = "https://api.mor.org" ]; then
    expected_source="https://active.mor.org/active_models.json"
    echo "  Environment: Production"
else
    expected_source="Unknown"
    echo "  Environment: Custom"
fi

actual_source=$(curl -s "$API_URL/health" 2>/dev/null | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    print(data.get('model_service', {}).get('active_models_url', 'N/A'))
except:
    print('N/A')
")

echo "  Expected Source: $expected_source"
echo "  Actual Source: $actual_source"

if [ "$actual_source" = "$expected_source" ]; then
    echo "  ✅ Configuration matches environment"
else
    echo "  ⚠️  Configuration mismatch"
fi
echo ""

echo "6. Quick Performance Check"
echo "--------------------------"
echo "🚀 Testing response times..."

# Test response time for health endpoint
start_time=$(date +%s%N)
curl -s "$API_URL/health" > /dev/null 2>&1
end_time=$(date +%s%N)
health_time=$(( (end_time - start_time) / 1000000 ))

echo "  Health endpoint: ${health_time}ms"

# Test response time for model health endpoint  
start_time=$(date +%s%N)
curl -s "$API_URL/health/models" > /dev/null 2>&1
end_time=$(date +%s%N)
model_health_time=$(( (end_time - start_time) / 1000000 ))

echo "  Model health endpoint: ${model_health_time}ms"

if [ "$model_health_time" -lt 1000 ]; then
    echo "  ✅ Good performance (likely cache hit)"
elif [ "$model_health_time" -lt 3000 ]; then
    echo "  ⚠️  Acceptable performance (possible cache miss)"
else
    echo "  ❌ Slow performance (investigate network/service issues)"
fi
echo ""

echo "================================================"
echo "✅ Quick health check completed!"
echo ""
echo "For detailed analysis, run:"
echo "  python scripts/verify_model_service.py --url $API_URL --verbose"
echo ""
echo "For ECS log analysis, run:"
echo "  python scripts/analyze_ecs_logs.py --log-group /aws/ecs/morpheus-api-dev --hours 1"
