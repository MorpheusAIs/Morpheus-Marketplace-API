"""
Structured logging configuration for Morpheus API using structlog.

This module provides centralized logging configuration with:
- JSON structured logging for production
- Console-friendly logging for development
- Component-specific log levels
- Proper context propagation
"""

import json
import logging
import os
import sys
from typing import Any, Dict, Optional

import structlog
from structlog.processors import JSONRenderer, KeyValueRenderer, TimeStamper
from structlog.stdlib import add_log_level, filter_by_level


class UvicornJSONFormatter(logging.Formatter):
    """
    Custom JSON formatter for uvicorn logs.
    
    Formats uvicorn standard and access logs as JSON when LOG_JSON=true.
    """
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        # Use ISO 8601 format with UTC timezone
        from datetime import datetime, timezone
        timestamp = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()
        
        # Get message, fallback to empty string if not available
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg) if hasattr(record, 'msg') else ""
        
        log_data = {
            "timestamp": timestamp,
            "level": record.levelname.lower() if record.levelname else "info",
            "logger": "core",  # Uvicorn logs are infrastructure/core
            "caller": f"{record.filename}:{record.lineno}" if hasattr(record, 'filename') else "",
            "event": message
        }

        # Parse uvicorn.access logs to extract structured data
        if record.name == "uvicorn.access" and hasattr(record, 'args') and len(record.args) >= 5:
            try:
                log_data["client_addr"] = str(record.args[0])
                log_data["method"] = str(record.args[1])
                log_data["endpoint"] = str(record.args[2])
                log_data["http_version"] = str(record.args[3])
                log_data["status_code"] = int(record.args[4])
                log_data["event"] = f"{log_data['method']} {log_data['endpoint']} - {log_data['status_code']}"
            except (IndexError, ValueError, TypeError):
                # If parsing fails, just use the original message
                pass

        # Add extra fields from the record (if present)
        if hasattr(record, "status_code") and "status_code" not in log_data:
            log_data["status_code"] = record.status_code
        if hasattr(record, "client_addr") and "client_addr" not in log_data:
            log_data["client_addr"] = record.client_addr
        if hasattr(record, "method") and "method" not in log_data:
            log_data["method"] = record.method
        if hasattr(record, "path") and "endpoint" not in log_data:
            log_data["endpoint"] = record.path
            
        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        # Filter out empty strings to reduce noise
        log_data = {k: v for k, v in log_data.items() if v != ""}
            
        return json.dumps(log_data)


class MorpheusLogConfig:
    """
    Centralized logging configuration for Morpheus API.
    
    Supports component-specific logging levels and both JSON and console output formats.
    """
    
    # Component hierarchy mapping for log level inheritance
    COMPONENT_HIERARCHY = {
        "CORE": ["uvicorn", "fastapi", "httpx", "asyncio", "sqlalchemy", "alembic", "httpcore"],
        "AUTH": ["cognito", "jwt", "api_key", "private_key", "boto3"],
        "PROXY": ["proxy_router", "upstream", "requests"],
        "MODELS": ["model_mapper", "model_sync", "model_routing", "direct_model"],
        "API": ["chat", "embeddings", "models", "sessions", "automation"],
    }
    
    def __init__(self):
        self.log_level = os.getenv("LOG_LEVEL", "INFO").upper()
        self.log_json = os.getenv("LOG_JSON", "true").lower() == "true"
        self.log_is_prod = os.getenv("LOG_IS_PROD", "false").lower() == "true"
        
        # Component-specific log levels
        self.component_levels = {
            "CORE": os.getenv("LOG_LEVEL_CORE", self.log_level).upper(),
            "AUTH": os.getenv("LOG_LEVEL_AUTH", self.log_level).upper(),
            "PROXY": os.getenv("LOG_LEVEL_PROXY", self.log_level).upper(),
            "MODELS": os.getenv("LOG_LEVEL_MODELS", self.log_level).upper(),
            "API": os.getenv("LOG_LEVEL_API", self.log_level).upper(),
        }
        
        self._configure_structlog()
        self._configure_stdlib_logging()
    
    def _configure_structlog(self):
        """Configure structlog with appropriate processors."""
        processors = [
            # Add log level to log entry
            add_log_level,
            # Add timestamp
            TimeStamper(fmt="iso", utc=True),
            # Filter by level before processing
            filter_by_level,
            # Ensure event field is populated
            self._ensure_event_field,
        ]
        
        if self.log_json:
            # JSON output for production
            processors.extend([
                self._add_logger_name,
                self._add_caller_info,
                JSONRenderer()
            ])
        else:
            # Console output for development
            processors.extend([
                self._add_logger_name,
                structlog.dev.ConsoleRenderer(colors=not self.log_is_prod)
            ])
        
        structlog.configure_once(
            processors=processors,
            wrapper_class=structlog.stdlib.BoundLogger,
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )
    
    def _configure_stdlib_logging(self):
        """Configure standard library logging to work with structlog."""
        # Set root logger level
        root_logger = logging.getLogger()
        root_logger.setLevel(getattr(logging, self.log_level))
        
        # Remove any existing handlers
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)
        
        # Add console handler for output
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(getattr(logging, self.log_level))
        
        # Set a basic formatter for the console handler
        # Let structlog handle the actual formatting
        console_handler.setFormatter(logging.Formatter('%(message)s'))
        
        root_logger.addHandler(console_handler)
        
        # Configure component-specific loggers
        self._configure_component_loggers()
        
        # Configure uvicorn-specific logging
        self._configure_uvicorn_logging()
    
    def _configure_component_loggers(self):
        """Configure log levels for component-specific loggers."""
        for component, level in self.component_levels.items():
            # Configure the component logger itself
            component_logger = logging.getLogger(component.lower())
            component_logger.setLevel(getattr(logging, level))
            
            # Configure related library loggers
            for lib_name in self.COMPONENT_HIERARCHY.get(component, []):
                lib_logger = logging.getLogger(lib_name)
                lib_logger.setLevel(getattr(logging, level))
    
    def _configure_uvicorn_logging(self):
        """Configure uvicorn loggers with JSON formatting when LOG_JSON=true."""
        uvicorn_loggers = [
            "uvicorn",
            "uvicorn.error",
            "uvicorn.access"
        ]
        
        for logger_name in uvicorn_loggers:
            logger = logging.getLogger(logger_name)
            
            # Only configure if JSON logging is enabled
            # Otherwise, let uvicorn use its default formatters
            if self.log_json:
                # Remove existing handlers
                for handler in logger.handlers[:]:
                    logger.removeHandler(handler)
                
                # Create new handler with JSON formatter
                handler = logging.StreamHandler(sys.stdout)
                handler.setFormatter(UvicornJSONFormatter(
                    fmt='%(asctime)s',
                    datefmt='%Y-%m-%dT%H:%M:%S'
                ))
                
                logger.addHandler(handler)
                logger.propagate = False  # Don't propagate to root logger
        
    @staticmethod
    def _add_logger_name(logger, name, event_dict):
        """Add logger name to event dict."""
        # If component is already bound (from get_component_logger), use it
        if "component" in event_dict:
            event_dict["logger"] = event_dict.pop("component").lower()
            return event_dict
        
        # Otherwise, extract component from logger name
        logger_name_lower = name.lower()
        
        # Check if logger name matches a component
        for component, libs in MorpheusLogConfig.COMPONENT_HIERARCHY.items():
            if logger_name_lower == component.lower() or any(lib in logger_name_lower for lib in libs):
                event_dict["logger"] = component.lower()
                return event_dict
        
        # Fallback: use the logger name as-is (lowercase for consistency)
        event_dict["logger"] = name.lower()
        return event_dict
    
    @staticmethod
    def _add_caller_info(logger, name, event_dict):
        """Add caller information to event dict."""
        try:
            import inspect
            frame = inspect.currentframe()
            # Go up the call stack to find the actual caller
            for _ in range(8):  # Skip structlog internal frames
                if frame is None:
                    break
                frame = frame.f_back
                if frame and frame.f_code.co_filename and 'structlog' not in frame.f_code.co_filename:
                    filename = frame.f_code.co_filename.split('/')[-1]
                    event_dict["caller"] = "{}:{}".format(filename, frame.f_lineno)
                    break
        except Exception:
            # Fallback if frame inspection fails
            event_dict["caller"] = "unknown"
        return event_dict
    
    @staticmethod
    def _ensure_event_field(logger, name, event_dict):
        """Ensure event field is populated (required for all logs)."""
        # Check if 'event' field exists and has content
        if not event_dict.get("event"):
            # Try alternative message fields in order of preference
            # 1. Check for 'message' field (common in many logging systems)
            if event_dict.get("message"):
                event_dict["event"] = str(event_dict["message"])
            # 2. Check for 'msg' field (common in structlog)
            elif event_dict.get("msg"):
                event_dict["event"] = str(event_dict["msg"])
            # 3. Check for '@message' field (CloudWatch specific)
            elif event_dict.get("@message"):
                event_dict["event"] = str(event_dict["@message"])
            # 4. Try to get message from stdlib logging record
            elif hasattr(logger, '_context') and hasattr(logger._context, 'msg'):
                event_dict["event"] = str(logger._context.msg)
            # Last resort: use a placeholder
            else:
                event_dict["event"] = "[no message]"
        return event_dict
    
    def get_logger(self, name: str) -> structlog.stdlib.BoundLogger:
        """
        Get a structured logger for the given name.
        
        Args:
            name: Logger name (typically __name__ or component name)
            
        Returns:
            Configured structlog logger
        """
        return structlog.get_logger(name)


# Global configuration instance
_log_config: Optional[MorpheusLogConfig] = None


def configure_logging() -> MorpheusLogConfig:
    """
    Configure logging for the entire application.
    
    This should be called once at application startup.
    
    Returns:
        Configured logging instance
    """
    global _log_config
    if _log_config is None:
        _log_config = MorpheusLogConfig()
    return _log_config


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """
    Get a structured logger for the given name.
    
    Args:
        name: Logger name (typically __name__ or component name)
        
    Returns:
        Configured structlog logger
    """
    if _log_config is None:
        configure_logging()
    return _log_config.get_logger(name)


def get_component_logger(component: str) -> structlog.stdlib.BoundLogger:
    """
    Get a logger for a specific component.
    
    Args:
        component: Component name (CORE, AUTH, PROXY, MODELS, API)
        
    Returns:
        Configured logger with component context
    """
    logger = get_logger(component.lower())
    return logger.bind(component=component.upper())


# Convenience functions for component-specific loggers
def get_core_logger() -> structlog.stdlib.BoundLogger:
    """Get logger for core infrastructure components."""
    return get_component_logger("CORE")


def get_auth_logger() -> structlog.stdlib.BoundLogger:
    """Get logger for authentication components."""
    return get_component_logger("AUTH")


def get_proxy_logger() -> structlog.stdlib.BoundLogger:
    """Get logger for proxy-router service components."""
    return get_component_logger("PROXY")


def get_models_logger() -> structlog.stdlib.BoundLogger:
    """Get logger for model-related components."""
    return get_component_logger("MODELS")


def get_api_logger() -> structlog.stdlib.BoundLogger:
    """Get logger for API endpoint components."""
    return get_component_logger("API")


def get_uvicorn_log_config() -> Optional[Dict[str, Any]]:
    """
    Get uvicorn logging configuration dictionary.
    
    Returns a logging config that can be passed to uvicorn.run() to ensure
    uvicorn uses the configured loggers and formatters.
    
    Note: This is only needed when running uvicorn directly. When using gunicorn,
    the _configure_uvicorn_logging() method handles configuration automatically.
    
    Returns:
        Dictionary with uvicorn logging configuration, or None to use uvicorn defaults
    """
    if _log_config is None:
        configure_logging()
    
    # Only return custom config if JSON logging is enabled
    # Otherwise, let uvicorn use its default configuration
    if not _log_config.log_json:
        return None
    
    log_level = _log_config.component_levels.get("CORE", _log_config.log_level)
    
    config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "()": "src.core.logging_config.UvicornJSONFormatter",
                "fmt": "%(asctime)s",
                "datefmt": "%Y-%m-%dT%H:%M:%S",
            },
            "access": {
                "()": "src.core.logging_config.UvicornJSONFormatter",
                "fmt": "%(asctime)s",
                "datefmt": "%Y-%m-%dT%H:%M:%S",
            },
        },
        "handlers": {
            "default": {
                "formatter": "default",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
            },
            "access": {
                "formatter": "access",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
            },
        },
        "loggers": {
            "uvicorn": {"handlers": ["default"], "level": log_level, "propagate": False},
            "uvicorn.error": {"handlers": ["default"], "level": log_level, "propagate": False},
            "uvicorn.access": {"handlers": ["access"], "level": log_level, "propagate": False},
        },
    }
    
    return config
