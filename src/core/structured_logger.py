"""
Zap-compatible structured logger for Morpheus Marketplace API.
Provides the same interface patterns as Morpheus-Lumerin-Node's Zap logger.
"""

import logging
from typing import Any, Dict, Optional, Union
from .logging_config import get_component_log_level


class ZapCompatibleLogger:
    """
    Logger that mimics Zap's interface and patterns from Morpheus-Lumerin-Node.
    
    Provides:
    - Named loggers (like zap's Named method)
    - Structured fields (like zap's With method)  
    - Format-style logging (like zap's Infof, Debugf, etc.)
    - Component-specific log levels
    """
    
    def __init__(self, name: str, component: Optional[str] = None):
        self.logger = logging.getLogger(name)
        self._context_fields = {}
        self._component = component or name.split('.')[-1].lower()
        
        # Set component-specific log level
        component_level = get_component_log_level(self._component)
        self.logger.setLevel(getattr(logging, component_level, logging.INFO))
    
    def named(self, name: str) -> 'ZapCompatibleLogger':
        """Create named logger (like zap's Named method)"""
        full_name = f"{self.logger.name}.{name}" if self.logger.name else name
        new_logger = ZapCompatibleLogger(full_name, self._component)
        new_logger._context_fields = self._context_fields.copy()
        return new_logger
    
    def with_fields(self, **kwargs) -> 'ZapCompatibleLogger':
        """Add structured fields (like zap's With method)"""
        new_logger = ZapCompatibleLogger(self.logger.name, self._component)
        new_logger._context_fields = {**self._context_fields, **kwargs}
        return new_logger
    
    def _log(self, level: str, msg: str, *args, **kwargs):
        """Internal logging method with structured fields"""
        # Merge context fields with any additional fields
        all_fields = {**self._context_fields, **kwargs}
        
        # Format message if args provided
        if args:
            try:
                msg = msg % args
            except (TypeError, ValueError):
                # If formatting fails, just append args
                msg = f"{msg} {' '.join(str(arg) for arg in args)}"
        
        # Create log record with structured fields
        extra = {'structured_fields': all_fields} if all_fields else {}
        
        getattr(self.logger, level.lower())(msg, extra=extra)
    
    # Standard logging methods (matching zap interface)
    def debug(self, msg: str, **kwargs):
        """Debug logging with optional structured fields"""
        self._log('DEBUG', msg, **kwargs)
        
    def debugf(self, template: str, *args, **kwargs):
        """Format-style debug (like zap's Debugf)"""
        self._log('DEBUG', template, *args, **kwargs)
    
    def info(self, msg: str, **kwargs):
        """Info logging with optional structured fields"""
        self._log('INFO', msg, **kwargs)
        
    def infof(self, template: str, *args, **kwargs):
        """Format-style info (like zap's Infof)"""
        self._log('INFO', template, *args, **kwargs)
    
    def warn(self, msg: str, **kwargs):
        """Warning logging with optional structured fields"""
        self._log('WARNING', msg, **kwargs)
        
    def warnf(self, template: str, *args, **kwargs):
        """Format-style warning (like zap's Warnf)"""
        self._log('WARNING', template, *args, **kwargs)
    
    def error(self, msg: str, **kwargs):
        """Error logging with optional structured fields"""
        self._log('ERROR', msg, **kwargs)
        
    def errorf(self, template: str, *args, **kwargs):
        """Format-style error (like zap's Errorf)"""
        self._log('ERROR', template, *args, **kwargs)
    
    # Business-specific convenience methods for common events
    def session_event(self, event: str, session_id: str = None, user_id: int = None, **kwargs):
        """Log session-related events with consistent structure"""
        self.with_fields(
            event_type="session",
            session_event=event,
            session_id=session_id,
            user_id=user_id,
            **kwargs
        ).info(f"Session {event}")
    
    def model_event(self, event: str, model_count: int = None, cache_hit: bool = None, **kwargs):
        """Log model-related events with consistent structure"""
        self.with_fields(
            event_type="model",
            model_event=event,
            model_count=model_count,
            cache_hit=cache_hit,
            **kwargs
        ).info(f"Model {event}")
    
    def http_request(self, method: str, endpoint: str, status_code: int = None, **kwargs):
        """Log HTTP request events with consistent structure"""
        self.with_fields(
            event_type="http_request",
            method=method,
            endpoint=endpoint,
            status_code=status_code,
            **kwargs
        ).info(f"HTTP {method} {endpoint}")
    
    def proxy_event(self, event: str, provider_id: str = None, **kwargs):
        """Log proxy-related events with consistent structure"""
        self.with_fields(
            event_type="proxy",
            proxy_event=event,
            provider_id=provider_id,
            **kwargs
        ).info(f"Proxy {event}")
    
    def database_event(self, event: str, table: str = None, record_count: int = None, **kwargs):
        """Log database-related events with consistent structure"""
        self.with_fields(
            event_type="database",
            db_event=event,
            table=table,
            record_count=record_count,
            **kwargs
        ).info(f"Database {event}")


# Global logger instances (like Lumerin Node's pattern)
def create_component_logger(component_name: str) -> ZapCompatibleLogger:
    """Create a component logger with proper naming"""
    return ZapCompatibleLogger(component_name.upper(), component_name.lower())


# Pre-configured component loggers (6 categories)
APP_LOG = create_component_logger("APP")        # Application-wide default
CORE_LOG = create_component_logger("CORE")      # Infrastructure (Uvicorn, FastAPI, HTTP, dependencies, local testing)
AUTH_LOG = create_component_logger("AUTH")      # Authentication (Cognito, JWT, API keys, private keys)  
DATABASE_LOG = create_component_logger("DATABASE") # All database operations
PROXY_LOG = create_component_logger("PROXY")    # Upstream calls to proxy-router API endpoints
MODELS_LOG = create_component_logger("MODELS")  # Model fetching, caching, routing
API_LOG = create_component_logger("API")        # Local API endpoints (chat, embeddings, models, sessions)
