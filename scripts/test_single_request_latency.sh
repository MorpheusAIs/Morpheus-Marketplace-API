#!/bin/bash
#
# Single Request Latency Test
# Measures timing at each stage of a chat completion request
#

set -e

# Configuration
API_URL="${API_URL:-https://api.stg.mor.org}"
API_KEY="${API_KEY:-}"
MODEL="${MODEL:-mistral-31-24b}"

if [ -z "$API_KEY" ]; then
    echo "Error: API_KEY environment variable must be set"
    exit 1
fi

# Generate unique request ID for tracking
REQUEST_ID=$(uuidgen | tr '[:upper:]' '[:lower:]' | cut -d'-' -f1)
TIMESTAMP_START=$(date +%s%3N)
TIMESTAMP_ISO=$(date -u +"%Y-%m-%dT%H:%M:%S.%3NZ")

echo "=================================="
echo "Single Request Latency Test"
echo "=================================="
echo "Request ID: $REQUEST_ID"
echo "Start Time: $TIMESTAMP_ISO ($TIMESTAMP_START ms)"
echo "API URL: $API_URL"
echo "Model: $MODEL"
echo ""

# Create request payload
PAYLOAD=$(cat <<EOF
{
  "model": "$MODEL",
  "messages": [
    {
      "role": "user",
      "content": "Hello, respond with exactly 5 words."
    }
  ],
  "stream": false,
  "max_tokens": 20
}
EOF
)

echo "Sending request..."
echo ""

# Make the request and capture timing
RESPONSE_FILE="/tmp/latency_test_${REQUEST_ID}.json"
TIMING_FILE="/tmp/latency_test_${REQUEST_ID}_timing.txt"

curl -w "\n\nHTTP Timing Metrics:\n  DNS Lookup: %{time_namelookup}s\n  TCP Connect: %{time_connect}s\n  TLS Handshake: %{time_appconnect}s\n  Time to First Byte: %{time_starttransfer}s\n  Total Time: %{time_total}s\n  HTTP Status: %{http_code}\n" \
  -o "$RESPONSE_FILE" \
  -s \
  -X POST \
  "$API_URL/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $API_KEY" \
  -H "X-Request-ID: $REQUEST_ID" \
  -d "$PAYLOAD" \
  2>&1 | tee "$TIMING_FILE"

TIMESTAMP_END=$(date +%s%3N)
TOTAL_LATENCY=$((TIMESTAMP_END - TIMESTAMP_START))

echo ""
echo "=================================="
echo "Client-Side Summary"
echo "=================================="
echo "Total Latency: ${TOTAL_LATENCY}ms"
echo ""

# Parse response
if [ -f "$RESPONSE_FILE" ]; then
    echo "Response:"
    cat "$RESPONSE_FILE" | jq '.'
    echo ""
    
    # Extract model and tokens if available
    MODEL_USED=$(cat "$RESPONSE_FILE" | jq -r '.model // "N/A"')
    TOKENS_INPUT=$(cat "$RESPONSE_FILE" | jq -r '.usage.prompt_tokens // "N/A"')
    TOKENS_OUTPUT=$(cat "$RESPONSE_FILE" | jq -r '.usage.completion_tokens // "N/A"')
    TOKENS_TOTAL=$(cat "$RESPONSE_FILE" | jq -r '.usage.total_tokens // "N/A"')
    
    echo "Model Used: $MODEL_USED"
    echo "Tokens - Input: $TOKENS_INPUT, Output: $TOKENS_OUTPUT, Total: $TOKENS_TOTAL"
fi

echo ""
echo "=================================="
echo "Next Steps: Check Server Logs"
echo "=================================="
echo ""
echo "STG API Logs (past 2 minutes):"
echo "  aws logs filter-log-events \\"
echo "    --log-group-name /aws/ecs/services/stg/morpheus-api \\"
echo "    --profile mor-org-prd \\"
echo "    --region us-east-2 \\"
echo "    --start-time $((TIMESTAMP_START - 5000)) \\"
echo "    --end-time $((TIMESTAMP_END + 5000)) \\"
echo "    --filter-pattern '\"$REQUEST_ID\"' \\"
echo "    --output json | jq -r '.events[] | .message'"
echo ""
echo "DEV C-Node Logs (past 2 minutes):"
echo "  aws logs filter-log-events \\"
echo "    --log-group-name /aws/ecs/services/dev/morpheus-router \\"
echo "    --profile mor-org-prd \\"
echo "    --region us-east-2 \\"
echo "    --start-time $((TIMESTAMP_START - 5000)) \\"
echo "    --end-time $((TIMESTAMP_END + 5000)) \\"
echo "    --output json | jq -r '.events[] | .message' | grep -A 5 -B 5 '$REQUEST_ID'"
echo ""
echo "Files saved:"
echo "  Response: $RESPONSE_FILE"
echo "  Timing: $TIMING_FILE"
