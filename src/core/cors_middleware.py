"""
Custom CORS middleware for ALB lb_cookie stickiness support.

This middleware ensures proper CORS handling for cross-origin requests
with credentials, specifically designed to work with AWS ALB sticky sessions.
"""

from fastapi import Request, Response
from fastapi.middleware.base import BaseHTTPMiddleware
from typing import List, Set, Optional
import logging
import re
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class CredentialSafeCORSMiddleware(BaseHTTPMiddleware):
    """
    Custom CORS middleware that safely handles credentials with explicit origin allowlists.
    
    Key features:
    - Never uses Access-Control-Allow-Origin: * with credentials
    - Always includes Vary: Origin to prevent cache poisoning
    - Properly handles preflight OPTIONS requests
    - Supports explicit origin allowlists only
    """
    
    def __init__(
        self,
        app,
        allowed_origins: List[str],
        allow_credentials: bool = True,
        allow_methods: List[str] = None,
        allow_headers: List[str] = None,
        expose_headers: List[str] = None,
        max_age: int = 86400,  # 24 hours
        trusted_domain_patterns: List[str] = None,  # Patterns for dynamic origin matching
        allow_direct_access: bool = True  # Allow direct API access from any origin
    ):
        super().__init__(app)
        
        # Validate that we don't have wildcards with credentials
        if allow_credentials and "*" in allowed_origins:
            raise ValueError(
                "Cannot use wildcard '*' in allowed_origins when allow_credentials=True. "
                "This is a security violation. Use explicit origins instead."
            )
        
        self.allowed_origins: Set[str] = set(allowed_origins)
        self.allow_credentials = allow_credentials
        self.allow_direct_access = allow_direct_access
        self.allow_methods = allow_methods or [
            "GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"
        ]
        self.allow_headers = allow_headers or [
            "Authorization", "Content-Type", "X-Requested-With", "X-API-Key"
        ]
        self.expose_headers = expose_headers or [
            "Content-Length", "Content-Type"
        ]
        self.max_age = max_age
        
        # Set up trusted domain patterns for dynamic origin matching
        self.trusted_domain_patterns = trusted_domain_patterns or [
            r"^https://.*\.mor\.org$",  # Any subdomain of mor.org
            r"^https://.*\.dev\.mor\.org$",  # Any subdomain of dev.mor.org
        ]
        self.compiled_patterns = [re.compile(pattern) for pattern in self.trusted_domain_patterns]
        
        # Separate localhost/development origins for better logging
        localhost_origins = [o for o in self.allowed_origins if 'localhost' in o or '127.0.0.1' in o]
        prod_origins = [o for o in self.allowed_origins if o not in localhost_origins]
        
        logger.info(
            f"CORS middleware initialized with {len(self.allowed_origins)} explicit origins"
        )
        if prod_origins:
            logger.info(f"Explicit production origins: {', '.join(sorted(prod_origins))}")
        if localhost_origins:
            logger.info(f"Explicit development origins: {', '.join(sorted(localhost_origins))}")
        
        logger.info(f"Trusted domain patterns: {', '.join(self.trusted_domain_patterns)}")
        logger.info(f"Direct API access allowed: {self.allow_direct_access}")
        
        if self.allow_direct_access:
            logger.warning(
                "⚠️  Direct API access is enabled - any origin can access with credentials. "
                "This is necessary for ALB cookie stickiness but reduces CORS security."
            )
    
    async def dispatch(self, request: Request, call_next):
        """Handle CORS for all requests"""
        
        # Get the origin from the request
        origin = request.headers.get("origin")
        
        # Always add Vary: Origin to prevent cache poisoning
        vary_header = "Origin"
        
        # Handle preflight OPTIONS requests
        if request.method == "OPTIONS":
            response = await self._handle_preflight(request, origin)
            response.headers["Vary"] = vary_header
            return response
        
        # Process the actual request
        response = await call_next(request)
        
        # Add CORS headers to the response
        self._add_cors_headers(response, origin)
        
        # Always add Vary: Origin
        existing_vary = response.headers.get("Vary", "")
        if existing_vary:
            if "Origin" not in existing_vary:
                response.headers["Vary"] = f"{existing_vary}, Origin"
        else:
            response.headers["Vary"] = vary_header
        
        return response
    
    async def _handle_preflight(self, request: Request, origin: str) -> Response:
        """Handle CORS preflight OPTIONS requests"""
        
        response = Response(status_code=204)  # No Content for preflight
        
        # Check if origin is allowed
        if origin and self.is_origin_allowed(origin):
            response.headers["Access-Control-Allow-Origin"] = origin
            
            if self.allow_credentials:
                response.headers["Access-Control-Allow-Credentials"] = "true"
        
        # Add preflight-specific headers
        response.headers["Access-Control-Allow-Methods"] = ", ".join(self.allow_methods)
        response.headers["Access-Control-Allow-Headers"] = ", ".join(self.allow_headers)
        response.headers["Access-Control-Max-Age"] = str(self.max_age)
        
        # Add exposed headers
        if self.expose_headers:
            response.headers["Access-Control-Expose-Headers"] = ", ".join(self.expose_headers)
        
        if origin:
            origin_type = self.get_origin_type(origin)
            if self.is_origin_allowed(origin):
                logger.debug(f"✅ Handled preflight request from {origin_type} origin: {origin}")
            else:
                logger.debug(f"❌ Blocked preflight request from {origin_type} origin: {origin}")
        else:
            logger.debug("Handled preflight request with no origin header")
        
        return response
    
    def _add_cors_headers(self, response: Response, origin: str):
        """Add CORS headers to actual responses"""
        
        # Only add CORS headers if the origin is allowed
        if origin and self.is_origin_allowed(origin):
            response.headers["Access-Control-Allow-Origin"] = origin
            
            if self.allow_credentials:
                response.headers["Access-Control-Allow-Credentials"] = "true"
            
            # Add exposed headers for actual responses
            if self.expose_headers:
                response.headers["Access-Control-Expose-Headers"] = ", ".join(self.expose_headers)
        
        # Note: We don't add Allow-Methods/Allow-Headers to actual responses,
        # only to preflight responses
    
    def is_origin_allowed(self, origin: str) -> bool:
        """Check if an origin should be allowed for CORS with credentials"""
        if not origin:
            return False
        
        # 1. Check explicit allowlist first
        if origin in self.allowed_origins:
            return True
        
        # 2. Check trusted domain patterns
        for pattern in self.compiled_patterns:
            if pattern.match(origin):
                logger.debug(f"Origin {origin} matched trusted pattern")
                return True
        
        # 3. If direct access is enabled, allow any HTTPS origin
        # This is necessary for ALB cookie stickiness from arbitrary clients
        if self.allow_direct_access:
            try:
                parsed = urlparse(origin)
                # Only allow HTTPS origins (except localhost for development)
                if parsed.scheme == 'https':
                    logger.debug(f"Allowing HTTPS origin for direct access: {origin}")
                    return True
                elif parsed.scheme == 'http' and (
                    parsed.hostname in ['localhost', '127.0.0.1'] or 
                    parsed.hostname.startswith('192.168.') or
                    parsed.hostname.startswith('10.') or
                    parsed.hostname.startswith('172.')
                ):
                    logger.debug(f"Allowing local HTTP origin for development: {origin}")
                    return True
            except Exception as e:
                logger.warning(f"Failed to parse origin {origin}: {e}")
                return False
        
        return False
    
    def get_origin_type(self, origin: str) -> str:
        """Get the type of origin for logging purposes"""
        if not origin:
            return "none"
        
        if origin in self.allowed_origins:
            return "explicit"
        
        for pattern in self.compiled_patterns:
            if pattern.match(origin):
                return "trusted_pattern"
        
        if self.allow_direct_access:
            try:
                parsed = urlparse(origin)
                if parsed.scheme == 'https':
                    return "direct_https"
                elif parsed.scheme == 'http':
                    return "direct_http"
            except:
                pass
        
        return "blocked"
