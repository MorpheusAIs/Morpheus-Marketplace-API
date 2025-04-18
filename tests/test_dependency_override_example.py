"""
Example of proper dependency overriding in FastAPI tests.

This demonstrates how to avoid the 'query.args' and 'query.kwargs' errors
that can occur when overriding dependencies.
"""
import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock

from src.main import app
from src.core.testing import create_dependency_override, create_return_value_override
from src.services.proxy_router import execute_proxy_router_operation
from src.crud import private_key as private_key_crud


@pytest.fixture
def test_client():
    """Create a test client with properly overridden dependencies."""
    # This is an example of how NOT to do it - this will cause the query.args/kwargs error
    # app.dependency_overrides[private_key_crud.get_decrypted_private_key] = MagicMock(return_value="test_key")
    
    # Instead, use the utility function to create a proper override
    app.dependency_overrides[private_key_crud.get_decrypted_private_key] = create_return_value_override("test_key")
    
    # Create the test client
    with TestClient(app) as client:
        yield client
    
    # Clean up after the test
    app.dependency_overrides.clear()


def test_example_endpoint(test_client):
    """Test an example endpoint that uses dependencies."""
    # This is just an example - adjust to your actual endpoints
    response = test_client.post(
        "/api/v1/session/approve",
        params={"spender": "0x123", "amount": 1000}
    )
    
    # Assert based on expected behavior
    # This test won't fail due to query.args/kwargs issues now
    assert response.status_code != 422, "Should not get a 422 validation error"


# Additional examples of proper overrides

def test_with_mock_db():
    """Example of mocking a database dependency."""
    # Create a mock database that returns a predefined value
    mock_db = MagicMock()
    
    # Wrap it properly to avoid the query.args/kwargs issue
    app.dependency_overrides[private_key_crud.get_db] = create_return_value_override(mock_db)
    
    # Test code here...
    
    # Clean up
    app.dependency_overrides.clear()


def test_with_custom_override():
    """Example with a more complex override function."""
    # Define a custom dependency override function
    def custom_override():
        """Custom override that returns specific data."""
        return {"custom": "data"}
    
    # Use it as a dependency override
    app.dependency_overrides[execute_proxy_router_operation] = custom_override
    
    # Test code here...
    
    # Clean up
    app.dependency_overrides.clear() 