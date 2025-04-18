# Guide to Dependency Overrides in FastAPI

## Understanding the query.args and query.kwargs Issue

When using dependency injection in FastAPI with the `Depends()` function, you may encounter errors about missing required query parameters `args` and `kwargs` when running tests or trying to use certain endpoints, especially with the private key integration.

### The Problem

This issue occurs due to how FastAPI inspects function signatures when processing dependencies:

1. When you use a dependency like `Depends(some_function)`, FastAPI examines the parameters of `some_function`.
2. When you override a dependency in tests with `app.dependency_overrides[some_function] = mock_object`, FastAPI examines the parameters of `mock_object`.
3. If you use `unittest.mock.MagicMock` directly, its signature is `(*args, **kwargs)`, which FastAPI interprets as required query parameters.
4. This causes FastAPI to expect query parameters named `args` and `kwargs` in your request, resulting in a 422 Unprocessable Entity error.

The error typically looks like:
```json
{
  "detail": [
    {
      "loc": ["query", "args"],
      "msg": "field required",
      "type": "value_error.missing"
    },
    {
      "loc": ["query", "kwargs"],
      "msg": "field required",
      "type": "value_error.missing"
    }
  ]
}
```

### Solution

To avoid this issue, never use `MagicMock` directly as a dependency override. Instead, use one of these approaches:

#### 1. Use a Lambda Function

Wrap your mock in a lambda function that has no parameters:

```python
app.dependency_overrides[some_function] = lambda: unittest.mock.MagicMock(return_value="some_value")
```

#### 2. Use Our Utility Functions

We've created utility functions in `src/core/testing.py` to make this easier:

```python
from src.core.testing import create_return_value_override

# Override a dependency to return a specific value
app.dependency_overrides[some_function] = create_return_value_override("some_value")
```

#### 3. Create a Simple Return Function

Define a function that returns what you need with no parameters:

```python
def override_func():
    return "some_value"

app.dependency_overrides[some_function] = override_func
```

### Working with Private Keys

When testing endpoints that require private keys, use the utility functions:

```python
from src.core.testing import mock_private_key_dependency

# Mock the private key for tests
app.dependency_overrides[private_key_crud.get_decrypted_private_key] = mock_private_key_dependency("test_private_key")
```

### Always Clean Up After Tests

In your test fixtures, make sure to clean up the overrides after each test:

```python
@pytest.fixture
def client():
    # Add overrides
    app.dependency_overrides[some_function] = create_return_value_override("some_value")
    
    with TestClient(app) as c:
        yield c
    
    # Clear all overrides
    app.dependency_overrides.clear()
```

## Example

See `tests/test_dependency_override_example.py` for a complete example of how to properly override dependencies in tests.

## Additional Resources

- [FastAPI Dependency Injection Documentation](https://fastapi.tiangolo.com/tutorial/dependencies/)
- [FastAPI Testing Documentation](https://fastapi.tiangolo.com/tutorial/testing/)
- [GitHub Issue Discussion](https://github.com/fastapi/fastapi/issues/3331) about this problem 