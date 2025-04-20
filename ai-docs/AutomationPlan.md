# Morpheus API Automation Plan

## Overview

This document outlines the implementation plan for the automation feature in the Morpheus API Gateway. The automation feature allows users to abstract away session creation and management when accessing the chat completions endpoint, providing a more seamless experience.

## Current Architecture

The current system requires manual session creation before using the chat completions endpoint:
1. Users create an account and get an API key
2. Users must explicitly create a session using the `/session/modelsession` endpoint
3. Sessions are associated with the API key in the database
4. The chat completions endpoint checks for an active session before processing requests

## Automation Feature Requirements

The automation feature will:
1. Allow users to enable/disable automatic session creation
2. Store automation preferences in the database
3. Automatically create sessions when needed for chat completions
4. Intelligently route requests to appropriate models based on a model routing configuration

## Implementation Status

### Completed Components:

1. ✅ Database Model: Added `UserAutomationSettings` model to store user automation preferences
2. ✅ Migration File: Created Alembic migration for `UserAutomationSettings` table
3. ✅ CRUD Operations: Implemented CRUD functions for automation settings
4. ✅ Model Router: Created `ModelRouter` class for mapping OpenAI/other model names to blockchain model IDs
5. ✅ Configuration: Added JSON-based model mappings config system
6. ✅ Feature Flag: Added system-wide `AUTOMATION_FEATURE_ENABLED` flag
7. ✅ API Endpoints: Implemented endpoints for getting/updating automation settings
8. ✅ Session Service: Created service for automated session creation
9. ✅ Chat Integration: Enhanced chat completions endpoint to support automated sessions
10. ✅ API Testing: API endpoints are accessible and properly configured
11. ✅ Unit Tests: Created tests for ModelRouter and CRUD operations
12. ✅ Integration Tests: Created tests for automated session creation
13. ✅ Deployment Plan: Created detailed deployment plan (see AutomationDeploymentPlan.md)
14. ✅ Execute Tests: Basic validation tests and simulated E2E tests completed successfully

### TODO:

1. ⛔ Finalize Database Migration: Apply the manual SQL migration in staging/production
2. ⛔ Deploy: Follow deployment plan steps in staging and production environments

### Testing Results

The following tests have been run and have passed:

1. Basic verification test (`test_automation.py`): This test verifies the presence of all necessary components and validates the model mappings configuration.
2. Simulated E2E test (`test_automation_e2e_simulated.py`): This test simulates the full automation flow without requiring a real server connection.

We have also implemented a real-server E2E test (`test_automation_e2e.py`) that can be run with proper API credentials to validate the automation feature against a live server.

### Migration Issue Workaround

Due to issues with the Alembic migration (transaction abortion), we've prepared a manual SQL script that can be used to create the required table:

```sql
-- Manual SQL to create user_automation_settings table
CREATE TABLE IF NOT EXISTS user_automation_settings (
    id SERIAL PRIMARY KEY,
    user_id INTEGER UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    is_enabled BOOLEAN DEFAULT FALSE,
    session_duration INTEGER DEFAULT 3600,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_user_automation_settings_id ON user_automation_settings (id);
```

This SQL can be executed directly on the database in testing and production environments.

## NOTE: Debugging Information

The implementation includes debug logging that should be removed for production:

1. Debug logs in `_handle_automated_session_creation` function
2. Debug logs in `create_automated_session` function

## Backward Compatibility

The implementation ensures backward compatibility by:
1. Setting automation to disabled by default for all users
2. Only introducing new behavior for users who explicitly opt-in
3. Maintaining the existing flow for users without automation enabled
4. Preserving all current endpoint behaviors and error messages for non-automated users

## Implementation Components

### 1. Database Schema for UserAutomationSettings

```python
class UserAutomationSettings(Base):
    __tablename__ = "user_automation_settings"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True)  # One automation setting per user
    is_enabled = Column(Boolean, default=False)  # Whether automation is enabled for this user (disabled by default)
    session_duration = Column(Integer, default=3600)  # Default session duration in seconds
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    user = relationship("User", back_populates="automation_settings")
```

This has been implemented in the database models and a migration has been created.

### 2. Model Routing with JSON Configuration

Model routing is implemented using a simple JSON configuration file in the `config/model_mappings.json`:

```json
{
  "default": "default-blockchain-model-id",
  "gpt-3.5-turbo": "compatible-model-blockchain-id-1",
  "gpt-4": "compatible-model-blockchain-id-2",
  "gpt-4o": "compatible-model-blockchain-id-3",
  "claude-3-opus": "compatible-model-blockchain-id-4"
}
```

This approach simplifies the implementation and makes it easier to update model mappings without database changes.

## Implementation Plan

### 1. Database Migration

✅ Created a new Alembic migration for the UserAutomationSettings table.

### 2. CRUD Operations

✅ Created CRUD operations for UserAutomationSettings in `src/crud/automation.py`.

### 3. Model Router Implementation 

✅ Created a dedicated module for model routing using a JSON configuration in `src/core/model_routing.py`.

### 4. API Endpoints for Automation Settings

✅ Created a new router file `src/api/v1/automation.py` with the following endpoints:

#### GET /automation/settings
- Get current automation settings for the authenticated user

#### PUT /automation/settings
- Update automation settings (enable/disable, session duration)

### 5. Feature Flag for System-Wide Control

✅ Added a new environment variable and configuration setting:

```python
# In core/config.py
AUTOMATION_FEATURE_ENABLED = os.getenv("AUTOMATION_FEATURE_ENABLED", "False").lower() == "true"
```

This allows for completely disabling the automation feature system-wide during testing or maintenance.

### 6. Chat Completions Endpoint Enhancement

✅ Modified the chat completions endpoint in `src/api/v1/chat.py` to support automated session creation when needed.

### 7. Session Service Enhancements

✅ Created a session service module in `src/services/session_service.py` for automated session creation.

## Model Mappings Configuration

✅ Created a config directory with a model_mappings.json file to map model names to blockchain IDs.

## Testing Plan

✅ Completed testing with simulated and basic verification tests.

1. ✅ Unit tests for the ModelRouter class
2. ✅ Unit tests for automation CRUD operations
3. ✅ Integration tests for the automation endpoints
4. ✅ End-to-end test for the automated session creation flow (simulated)
5. ✅ Test common error conditions and edge cases

## Deployment Strategy

TODO: Implement the deployment strategy.

1. Create initial model_mappings.json file with common mappings
2. Deploy database migrations for UserAutomationSettings
3. Deploy the new API endpoints with the system-wide feature flag disabled
4. Test in staging environment
5. Enable the feature flag for test accounts only
6. Gradually roll out to user groups
7. Monitor error rates and performance metrics
8. Fully roll out once stability is confirmed 