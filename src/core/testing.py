"""
Testing utilities for FastAPI dependency overrides.
"""
from typing import Any, Callable, Dict, Optional


def create_dependency_override() -> Callable:
    """
    Create a properly formatted dependency override function that won't cause
    'query.args' and 'query.kwargs' parameter errors.
    
    This function avoids the issue where MagicMock or functions with *args, **kwargs
    cause FastAPI to expect query parameters named 'args' and 'kwargs'.
    
    Returns:
        A callable that can be used as a dependency override
    """
    def override_func() -> Dict[str, Any]:
        """A simple override function with no parameters."""
        return {}
    
    return override_func


def create_return_value_override(return_value: Any) -> Callable:
    """
    Create a dependency override function that returns a specific value.
    
    Args:
        return_value: The value to return when the dependency is called
        
    Returns:
        A callable that can be used as a dependency override
    """
    def override_func() -> Any:
        """Returns the specified value."""
        return return_value
    
    return override_func


def mock_private_key_dependency(private_key: Optional[str] = None) -> Callable:
    """
    Create a mock for the private key dependency.
    
    Args:
        private_key: The private key to return, or None to simulate no key
        
    Returns:
        A callable that can be used as a dependency override
    """
    def override_func() -> Optional[str]:
        """Returns the specified private key."""
        return private_key
    
    return override_func 