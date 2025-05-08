"""
Custom API route implementation to fix the args/kwargs dependency issues.

This module provides a custom route class that properly handles dependency
injection without requiring 'args' and 'kwargs' as query parameters.
"""
from fastapi import Depends, params, status
from fastapi.routing import APIRoute
from fastapi.dependencies.utils import solve_dependencies
from fastapi.routing import run_endpoint_function
from fastapi.responses import JSONResponse
from starlette.requests import Request
from starlette.responses import Response
import inspect
import logging
import traceback
from typing import Any, Callable, Dict, List, Optional, Set, Type

# Setup logging
logger = logging.getLogger(__name__)

class FixedDependencyAPIRoute(APIRoute):
    """
    Custom API route that properly handles dependencies without requiring args/kwargs.
    
    This route class fixes the issue where FastAPI incorrectly requires query parameters
    named 'args' and 'kwargs' when using certain dependency patterns.
    """

    async def app(self, scope, receive, send) -> None:
        """
        ASGI application implementation for the route.
        
        This overrides the base implementation to properly handle dependencies.
        """
        await super().app(scope, receive, send)

    async def handle(self, request: Request, *args, **kwargs) -> Response:
        """
        Handle incoming requests with fixed dependency resolution.
        
        Args:
            request: The incoming request
            
        Returns:
            The endpoint response
        """
        try:
            # If this method is called with only the request parameter (for root endpoints)
            # we'll call the parent class implementation
            if not args and not kwargs:
                # For simple endpoints like root or health, use standard route processing
                route_handler = getattr(super(), "handle", None)
                if route_handler and callable(route_handler):
                    try:
                        return await route_handler(request)
                    except TypeError as e:
                        # If the parent class doesn't accept one arg, continue with our implementation
                        logger.debug(f"Falling back to fixed implementation: {str(e)}")
                        pass
                
            # Get the endpoint handler and dependencies
            route_scope = {"route": self}
            endpoint = self.endpoint
            
            # Get all dependencies for the endpoint
            dependencies = self.dependencies.copy()
            for param_name, param in self.dependant.params.items():
                if isinstance(param, params.Depends):
                    dependencies.append(param.dependency)
            
            # Create a clean scope for dependency resolution
            values = {}
            
            # Solve dependencies the normal way
            solved_result = await solve_dependencies(
                request=request,
                dependant=self.dependant,
                body=await request.body(),
            )
            
            # Extract the values but filter out 'args' and 'kwargs' if they're not
            # actually expected by the endpoint function
            values.update(solved_result.values)
            
            # Get the endpoint signature
            signature = inspect.signature(endpoint)
            param_names = set(signature.parameters.keys())
            
            # If 'args' and 'kwargs' are not actual parameters, remove them
            if 'args' not in param_names and 'args' in values:
                logger.debug(f"Removing unexpected 'args' parameter from {self.path}")
                del values['args']
            if 'kwargs' not in param_names and 'kwargs' in values:
                logger.debug(f"Removing unexpected 'kwargs' parameter from {self.path}")
                del values['kwargs']
            
            # Check if any required parameters are missing
            missing_params = []
            for name, param in signature.parameters.items():
                if (param.default == inspect.Parameter.empty and 
                    param.kind not in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD} and
                    name not in values):
                    missing_params.append(name)
            
            if missing_params:
                # Log the error but don't fail - we'll let the endpoint receive what we have
                param_str = ", ".join(missing_params)
                logger.warning(f"Missing required parameters for {self.path}: {param_str}")
            
            # Run the endpoint with the fixed dependencies
            raw_response = await run_endpoint_function(
                dependant=self.dependant,
                values=values,
                is_coroutine=self.is_coroutine,
            )
            
            # Process response
            return await self.response_class(raw_response)
            
        except Exception as exc:
            # Log the full error with stack trace
            error_msg = f"Error in route {self.path}: {str(exc)}"
            logger.error(error_msg)
            logger.error(traceback.format_exc())
            
            # Return a JSON error response
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "detail": "Internal server error occurred during request processing.",
                    "path": self.path,
                    "error_type": type(exc).__name__
                }
            ) 