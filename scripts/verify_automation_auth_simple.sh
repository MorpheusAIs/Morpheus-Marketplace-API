#!/bin/bash
# Simple verification script for automation endpoint authentication changes

echo "🔐 Morpheus API - Automation Authentication Verification"
echo "================================================================"
echo ""

AUTOMATION_FILE="src/api/v1/automation/index.py"
ERRORS=0
WARNINGS=0

# Check if file exists
if [ ! -f "$AUTOMATION_FILE" ]; then
    echo "❌ Error: $AUTOMATION_FILE not found"
    exit 1
fi

echo "📋 Checking $AUTOMATION_FILE..."
echo ""

# Check 1: Verify get_current_user import
echo "✓ Checking imports..."
if grep -q "from ....dependencies import get_current_user" "$AUTOMATION_FILE"; then
    echo "  ✅ Imports get_current_user"
else
    echo "  ❌ Missing get_current_user import"
    ERRORS=$((ERRORS + 1))
fi

# Check that old import is removed
if grep -q "get_api_key_user" "$AUTOMATION_FILE"; then
    echo "  ❌ Still has get_api_key_user (should be removed)"
    ERRORS=$((ERRORS + 1))
else
    echo "  ✅ No get_api_key_user import found"
fi

echo ""

# Check 2: Verify GET endpoint uses get_current_user
echo "✓ Checking GET endpoint..."
if grep -A 3 "@router.get(\"/settings\"" "$AUTOMATION_FILE" | grep -q "current_user: User = Depends(get_current_user)"; then
    echo "  ✅ GET endpoint uses get_current_user (JWT auth)"
else
    echo "  ❌ GET endpoint doesn't use get_current_user correctly"
    ERRORS=$((ERRORS + 1))
fi

# Check GET docstring mentions JWT
if grep -A 5 "@router.get(\"/settings\"" "$AUTOMATION_FILE" | grep -q "JWT Bearer authentication"; then
    echo "  ✅ GET docstring mentions JWT authentication"
else
    echo "  ⚠️  GET docstring should mention JWT authentication"
    WARNINGS=$((WARNINGS + 1))
fi

echo ""

# Check 3: Verify PUT endpoint uses get_current_user
echo "✓ Checking PUT endpoint..."
if grep -A 3 "@router.put(\"/settings\"" "$AUTOMATION_FILE" | grep -q "current_user: User = Depends(get_current_user)"; then
    echo "  ✅ PUT endpoint uses get_current_user (JWT auth)"
else
    echo "  ❌ PUT endpoint doesn't use get_current_user correctly"
    ERRORS=$((ERRORS + 1))
fi

# Check PUT docstring mentions JWT
if grep -A 5 "@router.put(\"/settings\"" "$AUTOMATION_FILE" | grep -q "JWT Bearer authentication"; then
    echo "  ✅ PUT docstring mentions JWT authentication"
else
    echo "  ⚠️  PUT docstring should mention JWT authentication"
    WARNINGS=$((WARNINGS + 1))
fi

echo ""

# Check 4: Verify current_user.id is used (not user.id)
echo "✓ Checking variable usage..."
if grep -q "current_user.id" "$AUTOMATION_FILE"; then
    echo "  ✅ Uses current_user.id correctly"
else
    echo "  ❌ Doesn't use current_user.id"
    ERRORS=$((ERRORS + 1))
fi

# Check that old user.id references are removed (except in comments)
if grep -v "^[[:space:]]*#" "$AUTOMATION_FILE" | grep -q "user\.id"; then
    echo "  ⚠️  Still has user.id references (should use current_user.id)"
    WARNINGS=$((WARNINGS + 1))
else
    echo "  ✅ No old user.id references found"
fi

echo ""
echo "================================================================"
echo "📊 VERIFICATION SUMMARY"
echo "================================================================"
echo ""

if [ $ERRORS -eq 0 ] && [ $WARNINGS -eq 0 ]; then
    echo "✅ All checks passed!"
    echo "   Automation endpoints are correctly configured for JWT authentication."
    echo ""
    exit 0
elif [ $ERRORS -eq 0 ]; then
    echo "⚠️  Passed with $WARNINGS warning(s)"
    echo "   Automation endpoints are functional but have minor issues."
    echo ""
    exit 0
else
    echo "❌ Failed with $ERRORS error(s) and $WARNINGS warning(s)"
    echo "   Automation endpoints have configuration errors."
    echo ""
    exit 1
fi

