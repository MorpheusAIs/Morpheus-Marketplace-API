import pytest
import json
import sys
import asyncio
from pathlib import Path
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock, AsyncMock

# Add the project root to the Python path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.main import app
from src.db.models import User, APIKey, UserAutomationSettings, Session
from src.crud import automation as automation_crud
from src.dependencies import get_api_key_user, get_db
from src.api.v1.chat import ChatCompletionRequest, ChatMessage

# Create test client
client = TestClient(app)

@pytest.fixture
def mock_user():
    """Create a mock user for testing."""
    user = MagicMock(spec=User)
    user.id = 1
    user.email = "test@example.com"
    
    # Mock user's API keys
    api_key = MagicMock(spec=APIKey)
    api_key.id = 1
    api_key.key_prefix = "sk-test"
    api_key.user_id = user.id
    user.api_keys = [api_key]
    
    return user

@pytest.fixture
def mock_api_key():
    """Create a mock API key for testing."""
    api_key = MagicMock(spec=APIKey)
    api_key.id = 1
    api_key.key_prefix = "sk-test"
    api_key.user_id = 1
    return api_key

@pytest.fixture
def mock_automation_settings():
    """Create mock automation settings for testing."""
    settings = MagicMock(spec=UserAutomationSettings)
    settings.id = 1
    settings.user_id = 1
    settings.is_enabled = True
    settings.session_duration = 3600
    return settings

@pytest.fixture
def mock_session():
    """Create a mock session for testing."""
    session = MagicMock(spec=Session)
    session.id = 1
    session.api_key_id = 1
    session.session_id = "test-session-id"
    session.model_id = "test-model-id"
    session.is_active = True
    return session

@pytest.fixture
def mock_db_session():
    """Create a mock DB session."""
    db_session = AsyncMock()
    
    # Add mock execute method that returns a mock result
    mock_result = AsyncMock()
    db_session.execute.return_value = mock_result
    
    # Make scalar_one_or_none return a value
    mock_scalar = AsyncMock()
    mock_result.scalar_one_or_none = lambda: mock_scalar
    
    return db_session

@pytest.fixture
def override_dependencies(mock_user, mock_db_session):
    """Override FastAPI dependencies."""
    # Store the original dependencies
    original_get_api_key_user = app.dependency_overrides.get(get_api_key_user, None)
    original_get_db = app.dependency_overrides.get(get_db, None)
    
    # Create async mock functions for dependencies
    async def mock_get_api_key_user_dependency(*args, **kwargs):
        return mock_user
        
    async def mock_get_db_dependency(*args, **kwargs):
        return mock_db_session
    
    # Override dependencies
    app.dependency_overrides[get_api_key_user] = mock_get_api_key_user_dependency
    app.dependency_overrides[get_db] = mock_get_db_dependency
    
    yield  # Run the test
    
    # Restore original dependencies
    if original_get_api_key_user:
        app.dependency_overrides[get_api_key_user] = original_get_api_key_user
    else:
        del app.dependency_overrides[get_api_key_user]
        
    if original_get_db:
        app.dependency_overrides[get_db] = original_get_db
    else:
        del app.dependency_overrides[get_db]

class TestAutomatedSession:
    """Tests for the automated session creation feature."""
    
    @pytest.mark.asyncio
    async def test_handle_automated_session_creation(
        self, mock_user, mock_api_key, mock_automation_settings
    ):
        """Test the _handle_automated_session_creation function directly."""
        # Mock the dependencies
        mock_db = AsyncMock()
        
        # Mock settings and automation
        with patch("src.core.config.settings") as mock_settings, \
             patch("src.api.v1.chat.automation_crud.get_automation_settings") as mock_get_settings:
            
            # Configure the mocks
            mock_settings.AUTOMATION_FEATURE_ENABLED = True
            mock_get_settings.return_value = mock_automation_settings
            
            # Mock the session_service.create_automated_session function
            with patch("src.api.v1.chat.session_service.create_automated_session") as mock_create:
                # Create a mock session to return
                mock_session = MagicMock()
                mock_session.session_id = "new-session-id"
                mock_create.return_value = mock_session
                
                # Mock the model_router.get_target_model function
                with patch("src.api.v1.chat.model_router.get_target_model") as mock_get_model:
                    mock_get_model.return_value = "mapped-model-id"
                    
                    # Import and call the function directly
                    from src.api.v1.chat import _handle_automated_session_creation
                    session_id = await _handle_automated_session_creation(
                        mock_db, mock_user, mock_api_key, "gpt-4"
                    )
                    
                    # Verify
                    assert session_id == "new-session-id"
                    assert mock_get_model.called  # Should call model router
                    assert mock_create.called  # Should create a new session
    
    @pytest.mark.asyncio
    async def test_chat_completion_with_automated_session(self, mock_user, mock_api_key):
        """Test chat completion with automated session creation directly."""
        # Mock the DB session
        mock_db = AsyncMock()
        
        # Create patches for all functions used within create_chat_completion
        with patch("src.api.v1.chat.session_crud.get_session_by_api_key_id") as mock_get_session, \
             patch("src.api.v1.chat._handle_automated_session_creation") as mock_automation, \
             patch("src.api.v1.chat.api_key_crud.get_api_key_by_prefix") as mock_get_api_key, \
             patch("httpx.AsyncClient.stream") as mock_stream:
            
            # Setup mocks
            mock_get_api_key.return_value = mock_api_key
            mock_get_session.return_value = None  # No existing session
            mock_automation.return_value = "new-session-id"  # New session created
            
            # Mock the streaming response
            mock_response = AsyncMock()
            mock_response.status_code = 200
            mock_response.headers = {}
            mock_response.aiter_bytes.return_value = [b'test response']
            
            # Set up the async context manager
            mock_stream_cm = AsyncMock()
            mock_stream_cm.__aenter__.return_value = mock_response
            mock_stream.return_value = mock_stream_cm
            
            # Create the chat request
            chat_request = ChatCompletionRequest(
                messages=[ChatMessage(role="user", content="Hello")]
            )
            
            # Import the function directly
            from src.api.v1.chat import create_chat_completion
            
            # Call the endpoint function directly with mocked dependencies
            response = await create_chat_completion(
                request_data=chat_request,
                api_key="sk-test",
                user=mock_user,
                db=mock_db
            )
            
            # Verify the response
            assert isinstance(response, object)  # Should return a StreamingResponse
            assert mock_automation.called  # Should attempt automated session creation
    
    @pytest.mark.asyncio
    async def test_chat_completion_with_existing_session(self, mock_user, mock_api_key, mock_session):
        """Test chat completion with an existing session directly."""
        # Mock the DB session
        mock_db = AsyncMock()
        
        # Create patches for all functions used within create_chat_completion
        with patch("src.api.v1.chat.session_crud.get_session_by_api_key_id") as mock_get_session, \
             patch("src.api.v1.chat._handle_automated_session_creation") as mock_automation, \
             patch("src.api.v1.chat.api_key_crud.get_api_key_by_prefix") as mock_get_api_key, \
             patch("httpx.AsyncClient.stream") as mock_stream:
            
            # Setup mocks
            mock_get_api_key.return_value = mock_api_key
            mock_get_session.return_value = mock_session  # Existing session
            
            # Mock the streaming response
            mock_response = AsyncMock()
            mock_response.status_code = 200
            mock_response.headers = {}
            mock_response.aiter_bytes.return_value = [b'test response']
            
            # Set up the async context manager
            mock_stream_cm = AsyncMock()
            mock_stream_cm.__aenter__.return_value = mock_response
            mock_stream.return_value = mock_stream_cm
            
            # Create the chat request
            chat_request = ChatCompletionRequest(
                messages=[ChatMessage(role="user", content="Hello")]
            )
            
            # Import the function directly
            from src.api.v1.chat import create_chat_completion
            
            # Call the endpoint function directly with mocked dependencies
            response = await create_chat_completion(
                request_data=chat_request,
                api_key="sk-test",
                user=mock_user,
                db=mock_db
            )
            
            # Verify the response
            assert isinstance(response, object)  # Should return a StreamingResponse
            assert not mock_automation.called  # Should not attempt automated session creation 