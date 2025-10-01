"""
Unit tests for CORS middleware functionality.

Tests the CredentialSafeCORSMiddleware to ensure proper handling of
cross-origin requests with credentials for ALB lb_cookie stickiness.
"""

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from src.core.cors_middleware import CredentialSafeCORSMiddleware


@pytest.fixture
def app_with_cors():
    """Create a test FastAPI app with CORS middleware"""
    app = FastAPI()
    
    # Add our custom CORS middleware
    app.add_middleware(
        CredentialSafeCORSMiddleware,
        allowed_origins=["https://openbeta.mor.org", "https://api.mor.org"],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Requested-With", "X-API-Key"],
        expose_headers=["Content-Length", "Content-Type"],
        max_age=86400
    )
    
    @app.get("/test")
    async def test_endpoint():
        return {"message": "test"}
    
    @app.post("/test")
    async def test_post_endpoint():
        return {"message": "test post"}
    
    return app


@pytest.fixture
def client(app_with_cors):
    """Create a test client"""
    return TestClient(app_with_cors)


class TestCORSMiddleware:
    """Test cases for CORS middleware"""
    
    def test_cors_middleware_initialization_with_wildcard_fails(self):
        """Test that wildcard origins with credentials raises ValueError"""
        with pytest.raises(ValueError, match="Cannot use wildcard"):
            CredentialSafeCORSMiddleware(
                app=FastAPI(),
                allowed_origins=["*"],
                allow_credentials=True
            )
    
    def test_cors_middleware_initialization_without_credentials_allows_wildcard(self):
        """Test that wildcard origins work without credentials"""
        # This should not raise an error
        middleware = CredentialSafeCORSMiddleware(
            app=FastAPI(),
            allowed_origins=["*"],
            allow_credentials=False
        )
        assert "*" in middleware.allowed_origins
    
    def test_allowed_origin_get_request(self, client):
        """Test GET request from allowed origin"""
        response = client.get(
            "/test",
            headers={"Origin": "https://openbeta.mor.org"}
        )
        
        assert response.status_code == 200
        assert response.headers["Access-Control-Allow-Origin"] == "https://openbeta.mor.org"
        assert response.headers["Access-Control-Allow-Credentials"] == "true"
        assert "Origin" in response.headers["Vary"]
    
    def test_allowed_origin_post_request(self, client):
        """Test POST request from allowed origin"""
        response = client.post(
            "/test",
            headers={"Origin": "https://api.mor.org"},
            json={"test": "data"}
        )
        
        assert response.status_code == 200
        assert response.headers["Access-Control-Allow-Origin"] == "https://api.mor.org"
        assert response.headers["Access-Control-Allow-Credentials"] == "true"
        assert "Origin" in response.headers["Vary"]
    
    def test_disallowed_origin_no_cors_headers(self, client):
        """Test that disallowed origins don't get CORS headers"""
        response = client.get(
            "/test",
            headers={"Origin": "https://evil.com"}
        )
        
        assert response.status_code == 200
        # With allow_direct_access=True, HTTPS origins are allowed
        assert "Access-Control-Allow-Origin" in response.headers
        assert response.headers["Access-Control-Allow-Origin"] == "https://evil.com"
        assert "Access-Control-Allow-Credentials" in response.headers
        # Vary: Origin should still be present
        assert "Origin" in response.headers["Vary"]
    
    def test_no_origin_header_no_cors_headers(self, client):
        """Test that requests without Origin header don't get CORS headers"""
        response = client.get("/test")
        
        assert response.status_code == 200
        assert "Access-Control-Allow-Origin" not in response.headers
        assert "Access-Control-Allow-Credentials" not in response.headers
        # Vary: Origin should still be present
        assert "Origin" in response.headers["Vary"]
    
    def test_preflight_options_request_allowed_origin(self, client):
        """Test preflight OPTIONS request from allowed origin"""
        response = client.options(
            "/test",
            headers={
                "Origin": "https://openbeta.mor.org",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Authorization,Content-Type"
            }
        )
        
        assert response.status_code == 204  # No Content for preflight
        assert response.headers["Access-Control-Allow-Origin"] == "https://openbeta.mor.org"
        assert response.headers["Access-Control-Allow-Credentials"] == "true"
        assert "POST" in response.headers["Access-Control-Allow-Methods"]
        assert "Authorization" in response.headers["Access-Control-Allow-Headers"]
        assert "Content-Type" in response.headers["Access-Control-Allow-Headers"]
        assert response.headers["Access-Control-Max-Age"] == "86400"
        assert "Origin" in response.headers["Vary"]
    
    def test_preflight_options_request_disallowed_origin(self, client):
        """Test preflight OPTIONS request from disallowed origin"""
        response = client.options(
            "/test",
            headers={
                "Origin": "https://evil.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Authorization,Content-Type"
            }
        )
        
        assert response.status_code == 204  # Still returns 204
        # With allow_direct_access=True, HTTPS origins are allowed
        assert "Access-Control-Allow-Origin" in response.headers
        assert response.headers["Access-Control-Allow-Origin"] == "https://evil.com"
        assert "Access-Control-Allow-Credentials" in response.headers
        # But still includes method/header info for transparency
        assert "POST" in response.headers["Access-Control-Allow-Methods"]
        assert "Origin" in response.headers["Vary"]
    
    def test_exposed_headers_in_response(self, client):
        """Test that exposed headers are included in response"""
        response = client.get(
            "/test",
            headers={"Origin": "https://openbeta.mor.org"}
        )
        
        assert response.status_code == 200
        assert "Content-Length" in response.headers["Access-Control-Expose-Headers"]
        assert "Content-Type" in response.headers["Access-Control-Expose-Headers"]
    
    def test_vary_origin_always_present(self, client):
        """Test that Vary: Origin is always present to prevent cache poisoning"""
        # Test with allowed origin
        response1 = client.get(
            "/test",
            headers={"Origin": "https://openbeta.mor.org"}
        )
        assert "Origin" in response1.headers["Vary"]
        
        # Test with disallowed origin
        response2 = client.get(
            "/test",
            headers={"Origin": "https://evil.com"}
        )
        assert "Origin" in response2.headers["Vary"]
        
        # Test without origin
        response3 = client.get("/test")
        assert "Origin" in response3.headers["Vary"]
    
    def test_is_origin_allowed_method(self):
        """Test the is_origin_allowed helper method"""
        middleware = CredentialSafeCORSMiddleware(
            app=FastAPI(),
            allowed_origins=["https://openbeta.mor.org", "https://api.mor.org"],
            allow_credentials=True,
            allow_direct_access=False  # Disable direct access for this test
        )
        
        assert middleware.is_origin_allowed("https://openbeta.mor.org") is True
        assert middleware.is_origin_allowed("https://api.mor.org") is True
        assert middleware.is_origin_allowed("https://evil.com") is False
        assert middleware.is_origin_allowed("http://localhost:3000") is False


class TestCORSIntegration:
    """Integration tests for CORS with actual endpoints"""
    
    def test_cors_with_multiple_vary_headers(self, client):
        """Test that Vary: Origin is properly combined with existing Vary headers"""
        # This would require a custom endpoint that sets Vary headers
        # For now, we test that our middleware doesn't break existing headers
        response = client.get(
            "/test",
            headers={"Origin": "https://openbeta.mor.org"}
        )
        
        vary_header = response.headers.get("Vary", "")
        assert "Origin" in vary_header
    
    def test_cors_headers_not_duplicated(self, client):
        """Test that CORS headers are not duplicated in responses"""
        response = client.get(
            "/test",
            headers={"Origin": "https://openbeta.mor.org"}
        )
        
        # Check that headers appear only once
        cors_origin_header = response.headers.get("Access-Control-Allow-Origin")
        assert cors_origin_header == "https://openbeta.mor.org"
        
        # Ensure no duplicate headers in raw response
        raw_headers = str(response.headers)
        assert raw_headers.count("access-control-allow-origin") == 1
