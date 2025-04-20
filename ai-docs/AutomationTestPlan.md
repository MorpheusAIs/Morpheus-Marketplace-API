# Morpheus API Automation Test Plan

This document outlines the comprehensive testing strategy for the automation feature in the Morpheus API Gateway.

## Test Environments

### Local Development
- Setup with Docker Compose
- Database: PostgreSQL in local container
- Feature flag: `AUTOMATION_FEATURE_ENABLED=true`
- Test user accounts with various permission levels

### CI/CD Pipeline
- Automated tests run on each PR
- Integration tests with in-memory database
- Environment variables set via CI configuration

### Staging
- Mirror of production environment
- Connected to separate test database
- Used for final verification before production deployment

## Test Categories

### Unit Tests

| Test ID | Description | Expected Result | Priority |
|---------|-------------|-----------------|----------|
| UT-01 | Test UserAutomationSettings model validation | All fields validated correctly | High |
| UT-02 | Test automation settings CRUD operations | Operations perform as expected | High |
| UT-03 | Test feature flag behavior | Feature flag controls access properly | High |
| UT-04 | Test session creation with automation | Sessions created with correct parameters | High |
| UT-05 | Test session expiration logic | Sessions expire after configured time | Medium |
| UT-06 | Test user permissions for automation | Only authorized users can access | High |

### API Tests

| Test ID | Description | Expected Result | Priority |
|---------|-------------|-----------------|----------|
| API-01 | GET /api/v1/automation/settings | Returns user settings or default | High |
| API-02 | POST /api/v1/automation/settings | Creates/updates settings | High |
| API-03 | DELETE /api/v1/automation/settings | Removes user settings | Medium |
| API-04 | Access endpoints with feature flag off | Return 404 Not Found | High |
| API-05 | Access endpoints without authentication | Return 401 Unauthorized | Critical |
| API-06 | Access endpoints with invalid token | Return 401 Unauthorized | Critical |
| API-07 | POST with invalid JSON payload | Return 400 Bad Request | Medium |
| API-08 | POST with missing required fields | Return 400 Bad Request | Medium |

### Integration Tests

| Test ID | Description | Expected Result | Priority |
|---------|-------------|-----------------|----------|
| INT-01 | Chat completion with automation on | Session created automatically | High |
| INT-02 | Chat completion with automation off | No session created | High |
| INT-03 | Multiple concurrent requests | All requests handled correctly | Medium |
| INT-04 | Integration with user service | User data retrieved correctly | Medium |
| INT-05 | Integration with session service | Sessions managed correctly | High |
| INT-06 | Database transaction rollback | Failed operations don't persist | High |

### Performance Tests

| Test ID | Description | Expected Result | Priority |
|---------|-------------|-----------------|----------|
| PERF-01 | Load test automation endpoints | Response time < 200ms under load | Medium |
| PERF-02 | Database performance with many settings | Queries complete in < 50ms | Medium |
| PERF-03 | Session creation performance | Sessions created in < 100ms | Medium |
| PERF-04 | Concurrent users accessing automation | System scales appropriately | Medium |

### Security Tests

| Test ID | Description | Expected Result | Priority |
|---------|-------------|-----------------|----------|
| SEC-01 | SQL injection attempts | All prevented, 400 responses | Critical |
| SEC-02 | Cross-site scripting attempts | All prevented, 400 responses | Critical |
| SEC-03 | Unauthorized access attempts | Return 401/403 as appropriate | Critical |
| SEC-04 | Rate limiting tests | Requests limited after threshold | High |
| SEC-05 | Sensitive data exposure | No PII in logs or responses | Critical |

## Automated Testing Process

### Running Unit Tests
```bash
# Run all tests
python -m pytest tests/unit/

# Run specific test file
python -m pytest tests/unit/test_automation_settings.py

# Run with coverage
python -m pytest tests/unit/ --cov=app
```

### Running API Tests
```bash
# Run all API tests
python -m pytest tests/api/

# Run specific API test
python -m pytest tests/api/test_automation_endpoints.py

# Run with verbose output
python -m pytest tests/api/ -v
```

### Running the Full Test Suite
```bash
# Run the complete test script
python run_tests.py
```

## Manual Testing Scenarios

### User Settings Management

1. **Create New Settings**
   - Steps:
     1. Log in as a test user
     2. Send POST to /api/v1/automation/settings with valid payload
     3. Verify 200 response
     4. GET settings to confirm they were saved
   - Expected Result: Settings saved and retrievable

2. **Update Existing Settings**
   - Steps:
     1. Log in as a user with existing settings
     2. Send POST with modified payload
     3. Verify 200 response
     4. GET settings to confirm updates
   - Expected Result: Settings updated correctly

3. **Delete Settings**
   - Steps:
     1. Log in as a user with existing settings
     2. Send DELETE to /api/v1/automation/settings
     3. Verify 200 response
     4. GET settings to confirm deletion
   - Expected Result: Settings removed, default returned

### Feature Flag Testing

1. **Feature Flag Enabled**
   - Steps:
     1. Set AUTOMATION_FEATURE_ENABLED=true
     2. Restart service
     3. Test all automation endpoints
   - Expected Result: All endpoints accessible

2. **Feature Flag Disabled**
   - Steps:
     1. Set AUTOMATION_FEATURE_ENABLED=false
     2. Restart service
     3. Test all automation endpoints
   - Expected Result: All automation endpoints return 404

### Session Creation Testing

1. **Automatic Session Creation**
   - Steps:
     1. Enable automation for test user
     2. Send chat completion request
     3. Check that session was created
     4. Verify session parameters match settings
   - Expected Result: Session created automatically with correct settings

2. **Manual Session Override**
   - Steps:
     1. Enable automation for test user
     2. Send chat completion with explicit session_id
     3. Verify request uses provided session_id
   - Expected Result: Provided session_id takes precedence over automation

## Test Data Management

### Test User Accounts
- Admin user: Full permissions
- Standard user: Can modify own settings
- Read-only user: Cannot modify settings
- Unauthenticated: No access

### Test Databases
- Tests run against a separate test database
- Database reset before each test run
- Migrations applied automatically

## Regression Testing

Before each release, the following areas must be regression tested:

1. All existing API endpoints continue to function
2. User authentication works properly
3. Session management unaffected
4. Performance remains within acceptable parameters

## Test Reporting

Test results should be reported in the following format:

```
Test Suite: [Name]
Date: [Date]
Environment: [Environment]

Summary:
- Total Tests: [Number]
- Passed: [Number]
- Failed: [Number]
- Skipped: [Number]

Failed Tests:
- [Test ID]: [Failure Reason]
- [Test ID]: [Failure Reason]

Performance Metrics:
- Average Response Time: [Time]
- 95th Percentile: [Time]
- Max Response Time: [Time]
```

## Exit Criteria

Testing is considered complete when:

1. All unit tests pass with at least 90% code coverage
2. All API tests pass in the staging environment
3. No critical or high-priority bugs remain open
4. Performance tests show acceptable response times
5. Security tests find no vulnerabilities

## Issue Tracking

All issues found during testing should be logged with:

1. Test ID that found the issue
2. Detailed reproduction steps
3. Expected vs. actual results
4. Environment information
5. Priority and severity assessment

## Responsibilities

- **QA Engineer**: Execute test plan, report issues
- **Developer**: Fix reported issues, write unit tests
- **DevOps**: Maintain test environments
- **Product Manager**: Sign off on test results before deployment 