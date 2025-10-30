# Private Key Integration with Proxy Router

## Current System Analysis

### Architecture Overview
- Users store their blockchain private keys via `/auth/private-key` endpoint
- Private keys are encrypted and stored in the database
- A function `get_decrypted_private_key` exists to retrieve and decrypt the keys
- Sessions are created through the proxy router for blockchain operations
- API keys are linked to users and sessions in the database

### Issues Identified
1. **Missing Private Key Integration**: While we store private keys, they aren't being passed to the proxy router
2. **FastAPI Dependency Errors**: 422 errors occurring with "query.args" and "query.kwargs"
3. **Incomplete Flow**: No logic to associate private keys with sessions during operations

## Recommended Solution: HTTP Header Approach

After evaluating all options, we recommend implementing the HTTP Header approach as it provides the best balance of security, compatibility, and maintainability given the constraint that the proxy router code (Morpheus-Lumerin-Node) cannot be directly modified.

### Implementation: Pass Private Key via HTTP Header

This approach passes the private key via a custom HTTP header to the proxy router, allowing the proxy router to use it for blockchain operations without requiring code modifications if it already supports reading from headers.

```python
async def execute_proxy_router_operation(endpoint, data, user_id, db):
    # Get user's private key
    private_key = await get_decrypted_private_key(db, user_id)
    if not private_key:
        raise HTTPException(status_code=400, detail="Private key not found")
    
    # Execute the call to proxy router with private key in header
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{settings.PROXY_ROUTER_URL}/{endpoint}",
            json=data,
            headers={"X-Private-Key": private_key},
            auth=(settings.PROXY_ROUTER_USERNAME, settings.PROXY_ROUTER_PASSWORD),
            timeout=30.0
        )
        response.raise_for_status()
        return response.json()
```

### Advantages of this Approach:
- **No Proxy Router Modification Required**: Works with existing proxy router configurations that support header-based authentication
- **Cross-Machine Compatible**: Can function even if API and proxy router are on different servers
- **Per-Request Isolation**: Each request gets its own private key context, avoiding concurrency issues
- **Straightforward Implementation**: Requires minimal changes to the existing API code

### Configuration Requirements:
1. Ensure the proxy router is configured to accept and use the `X-Private-Key` header (or whatever header name is appropriate)
2. Set up secure TLS for all communications between the API and proxy router
3. Implement proper logging (without logging the actual private key content)

### 2. Fix FastAPI Dependency Errors

The custom route handler in `main.py` is attempting to fix "query.args" and "query.kwargs" errors but isn't working correctly.

```python
# Update the CustomRoute class in main.py
class CustomRoute(APIRoute):
    def get_route_handler(self):
        original_route_handler = super().get_route_handler()

        async def custom_route_handler(request: Request):
            try:
                # Properly patch request before any processing
                scope = dict(request.scope)
                
                # Always add args and kwargs to query parameters
                query_params = dict(request.query_params)
                if "args" not in query_params:
                    query_params["args"] = ""
                if "kwargs" not in query_params:
                    query_params["kwargs"] = ""
                
                # Rebuild query string
                query_string = "&".join([f"{k}={v}" for k, v in query_params.items()])
                scope["query_string"] = query_string.encode()
                
                # Create patched request
                patched_request = Request(scope=scope, receive=request.receive)
                
                # Process with patched request
                return await original_route_handler(patched_request)
            except Exception as exc:
                # If error persists, log and re-raise
                logger.error(f"Error processing request: {str(exc)}")
                raise
                
        return custom_route_handler
```

### 3. Implementation Plan for Session Endpoints

Update the key session endpoints to properly integrate private keys using the HTTP header approach:

#### A. Update `/approve` Endpoint

```python
@router.post("/approve")
async def approve_spending(
    spender: str = Query(...),
    amount: int = Query(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_api_key_user)
):
    # Get user's private key
    private_key = await private_key_crud.get_decrypted_private_key(db, user.id)
    if not private_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No private key found for user. Please set up your private key first."
        )
    
    try:
        # Make the proxy router call with private key in header
        endpoint = f"{settings.PROXY_ROUTER_URL}/blockchain/approve"
        params = {"spender": spender, "amount": amount}
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                endpoint,
                params=params,
                headers={"X-Private-Key": private_key},
                auth=(settings.PROXY_ROUTER_USERNAME, settings.PROXY_ROUTER_PASSWORD),
                timeout=30.0
            )
            response.raise_for_status()
            
            return response.json()
    except Exception as e:
        handle_proxy_error(e, "approving spending")
```

Similar updates should be made to `/bidsession` and `/modelsession` endpoints.

## Implementation Phases

### Phase 1: Create Helper Functions
1. Create utility functions for proxy router integration with private keys
2. Fix the CustomRoute class in main.py to properly handle args/kwargs

### Phase 2: Update Session Endpoints
1. Modify `/approve` endpoint to use private keys
2. Update `/bidsession` endpoint to use private keys
3. Update `/modelsession` endpoint to use private keys

### Phase 3: Testing and Documentation
1. Test with sample private keys
2. Document the complete flow from API key → user → private key → proxy router
3. Add error handling and logging

## Security Considerations
- Ensure private keys are never logged or stored in plaintext
- All communication between API and proxy router MUST use TLS/HTTPS
- Consider adding timeouts or rate limits for operations using private keys
- Add monitoring for suspicious operations
- Consider implementing transaction confirmation for sensitive operations
- Use the most minimal scope possible when passing private keys

## Long-term Improvements
1. Consider implementing a proper key management service (AWS KMS, HashiCorp Vault)
2. Add key rotation capabilities
3. Implement additional authorization checks before using private keys
4. Consider redesigning the proxy router to use a more secure method for key management

## Alternative Approaches (Not Recommended)

### Option A: Environment Variable Injection (Not Selected)
This approach would dynamically update the proxy router's environment with each user's private key.

```python
async def execute_proxy_router_operation(endpoint, data, user_id, db):
    # Get user's private key
    private_key = await get_decrypted_private_key(db, user_id)
    if not private_key:
        raise HTTPException(status_code=400, detail="Private key not found")
    
    # Original proxy router environment variables
    original_env = os.environ.copy()
    
    try:
        # Set private key in environment for this specific call
        os.environ["PRIVATE_KEY"] = private_key
        
        # Execute the call to proxy router
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{settings.PROXY_ROUTER_URL}/{endpoint}",
                json=data,
                auth=(settings.PROXY_ROUTER_USERNAME, settings.PROXY_ROUTER_PASSWORD),
                timeout=30.0
            )
            response.raise_for_status()
            return response.json()
    finally:
        # Restore original environment
        os.environ.clear()
        os.environ.update(original_env)
```

**Not selected because**:
- Only works if both services are on the same VM
- Environment variables are global, potentially causing concurrency issues with multiple users
- The proxy router would need to be configured to read environment variables for each request rather than just at startup
- Manipulating environment variables at runtime is generally discouraged for security reasons

### Option C: Shared Key Vault Service (Not Selected)
If both services run on the same VM, modify the proxy router to access the key vault directly:

1. Implement a shared key vault service accessible by both applications
2. Have the proxy router request keys from this service when needed

**Not selected because**:
- Requires modifying the Morpheus-Lumerin-Node code, which is an external repository that cannot be directly changed
- Significantly more complex to implement
- Would require designing and maintaining an additional service
- Would require significant architectural changes to both systems 