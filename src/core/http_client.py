"""
Context-aware HTTP client wrapper for business intelligence and troubleshooting.

This module provides HTTP clients that automatically categorize requests by business context:
- AUTH.COGNITO: Authentication and JWT validation
- MODELS.HTTP: Model fetching and caching  
- PROXY.HTTP: Proxy router communication
- CORE.HTTP: General infrastructure calls
"""

import httpx
import logging
from typing import Optional, Dict, Any, Union
from urllib.parse import urlparse
from contextlib import contextmanager

from .structured_logger import AUTH_LOG, MODELS_LOG, PROXY_LOG, CORE_LOG


class HTTPLogInterceptor(logging.Handler):
    """Custom logging handler that intercepts and re-routes HTTP library logs"""
    
    def __init__(self, target_logger, context: str):
        super().__init__()
        self.target_logger = target_logger
        self.context = context
        self.setLevel(logging.DEBUG)
    
    def emit(self, record):
        # Create a new log record with our target logger
        new_record = logging.LogRecord(
            name=f"{self.target_logger.logger.name}.HTTP",
            level=record.levelno,
            pathname=record.pathname,
            lineno=record.lineno,
            msg=record.getMessage(),
            args=(),
            exc_info=record.exc_info,
            func=record.funcName,
            stack_info=record.stack_info
        )
        
        # Add structured fields if they exist
        if hasattr(record, 'structured_fields'):
            new_record.structured_fields = record.structured_fields
        
        # Emit through our target logger
        self.target_logger.logger.handle(new_record)


@contextmanager
def http_log_context(target_logger, context: str):
    """Context manager that intercepts HTTP logs and routes them to the target logger"""
    # Create interceptor handlers
    httpx_interceptor = HTTPLogInterceptor(target_logger, context)
    httpcore_interceptor = HTTPLogInterceptor(target_logger, context)
    httpcore_http11_interceptor = HTTPLogInterceptor(target_logger, context)
    botocore_interceptor = HTTPLogInterceptor(target_logger, context)
    botocore_endpoint_interceptor = HTTPLogInterceptor(target_logger, context)
    botocore_hooks_interceptor = HTTPLogInterceptor(target_logger, context)
    botocore_regions_interceptor = HTTPLogInterceptor(target_logger, context)
    passlib_interceptor = HTTPLogInterceptor(target_logger, context)
    passlib_bcrypt_interceptor = HTTPLogInterceptor(target_logger, context)
    
    # Get the HTTP library loggers
    httpx_logger = logging.getLogger("httpx")
    httpcore_logger = logging.getLogger("httpcore.connection")
    httpcore_http11_logger = logging.getLogger("httpcore.http11")
    botocore_logger = logging.getLogger("botocore")
    botocore_endpoint_logger = logging.getLogger("botocore.endpoint")
    botocore_hooks_logger = logging.getLogger("botocore.hooks")
    botocore_regions_logger = logging.getLogger("botocore.regions")
    passlib_logger = logging.getLogger("passlib")
    passlib_bcrypt_logger = logging.getLogger("passlib.handlers.bcrypt")
    
    # Add our interceptors
    httpx_logger.addHandler(httpx_interceptor)
    httpcore_logger.addHandler(httpcore_interceptor)
    httpcore_http11_logger.addHandler(httpcore_http11_interceptor)
    botocore_logger.addHandler(botocore_interceptor)
    botocore_endpoint_logger.addHandler(botocore_endpoint_interceptor)
    botocore_hooks_logger.addHandler(botocore_hooks_interceptor)
    botocore_regions_logger.addHandler(botocore_regions_interceptor)
    passlib_logger.addHandler(passlib_interceptor)
    passlib_bcrypt_logger.addHandler(passlib_bcrypt_interceptor)
    
    try:
        yield
    finally:
        # Remove our interceptors
        httpx_logger.removeHandler(httpx_interceptor)
        httpcore_logger.removeHandler(httpcore_interceptor)
        httpcore_http11_logger.removeHandler(httpcore_http11_interceptor)
        botocore_logger.removeHandler(botocore_interceptor)
        botocore_endpoint_logger.removeHandler(botocore_endpoint_interceptor)
        botocore_hooks_logger.removeHandler(botocore_hooks_interceptor)
        botocore_regions_logger.removeHandler(botocore_regions_interceptor)
        passlib_logger.removeHandler(passlib_interceptor)
        passlib_bcrypt_logger.removeHandler(passlib_bcrypt_interceptor)


class ContextAwareHTTPClient:
    """HTTP client that logs requests under the appropriate business category"""
    
    def __init__(self, context: str, base_logger, timeout: float = 30.0):
        """
        Initialize context-aware HTTP client.
        
        Args:
            context: Business context (e.g., "COGNITO", "MODEL_SERVICE", "PROXY_ROUTER")
            base_logger: Base logger instance (AUTH_LOG, MODELS_LOG, etc.)
            timeout: Default timeout for requests
        """
        self.context = context
        self.logger = base_logger.named(context)
        self.client = httpx.AsyncClient(timeout=timeout)
        
    async def request(
        self, 
        method: str, 
        url: str, 
        **kwargs
    ) -> httpx.Response:
        """
        Make an HTTP request with business context logging.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            url: Request URL
            **kwargs: Additional arguments passed to httpx
            
        Returns:
            httpx.Response object
        """
        parsed_url = urlparse(url)
        host = parsed_url.netloc
        path = parsed_url.path
        
        # Log the request initiation
        self.logger.with_fields(
            event_type="http_request_start",
            method=method,
            host=host,
            path=path,
            url=url
        ).infof("HTTP %s %s", method, url)
        
        try:
            # Use context manager to intercept HTTP logs
            with http_log_context(self.logger, self.context):
                response = await self.client.request(method, url, **kwargs)
            
            # Log successful response
            self.logger.with_fields(
                event_type="http_request_complete",
                method=method,
                host=host,
                path=path,
                status_code=response.status_code,
                response_time_ms=response.elapsed.total_seconds() * 1000 if response.elapsed else None
            ).infof("HTTP %s %s → %d", method, url, response.status_code)
            
            return response
            
        except httpx.HTTPStatusError as e:
            self.logger.with_fields(
                event_type="http_request_error",
                method=method,
                host=host,
                path=path,
                status_code=e.response.status_code,
                error=str(e)
            ).errorf("HTTP %s %s → %d: %s", method, url, e.response.status_code, str(e))
            raise
            
        except httpx.RequestError as e:
            self.logger.with_fields(
                event_type="http_request_error",
                method=method,
                host=host,
                path=path,
                error=str(e)
            ).errorf("HTTP %s %s failed: %s", method, url, str(e))
            raise
    
    async def get(self, url: str, **kwargs) -> httpx.Response:
        """GET request with context logging"""
        return await self.request("GET", url, **kwargs)
    
    async def post(self, url: str, **kwargs) -> httpx.Response:
        """POST request with context logging"""
        return await self.request("POST", url, **kwargs)
    
    async def put(self, url: str, **kwargs) -> httpx.Response:
        """PUT request with context logging"""
        return await self.request("PUT", url, **kwargs)
    
    async def delete(self, url: str, **kwargs) -> httpx.Response:
        """DELETE request with context logging"""
        return await self.request("DELETE", url, **kwargs)
    
    
    async def close(self):
        """Close the HTTP client"""
        await self.client.aclose()
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()


# Pre-configured HTTP clients for different business contexts
auth_http_client = ContextAwareHTTPClient("COGNITO", AUTH_LOG, timeout=10.0)
models_http_client = ContextAwareHTTPClient("MODEL_SERVICE", MODELS_LOG, timeout=30.0)
proxy_http_client = ContextAwareHTTPClient("PROXY_ROUTER", PROXY_LOG, timeout=30.0)
core_http_client = ContextAwareHTTPClient("INFRASTRUCTURE", CORE_LOG, timeout=30.0)


# Convenience functions for business-specific HTTP calls
async def cognito_request(method: str, url: str, **kwargs) -> httpx.Response:
    """Make HTTP request for Cognito authentication with AUTH categorization"""
    return await auth_http_client.request(method, url, **kwargs)

async def model_service_request(method: str, url: str, **kwargs) -> httpx.Response:
    """Make HTTP request for model service with MODELS categorization"""
    return await models_http_client.request(method, url, **kwargs)

async def proxy_router_request(method: str, url: str, **kwargs) -> httpx.Response:
    """Make HTTP request for proxy router with PROXY categorization"""
    return await proxy_http_client.request(method, url, **kwargs)

async def infrastructure_request(method: str, url: str, **kwargs) -> httpx.Response:
    """Make HTTP request for general infrastructure with CORE categorization"""
    return await core_http_client.request(method, url, **kwargs)
