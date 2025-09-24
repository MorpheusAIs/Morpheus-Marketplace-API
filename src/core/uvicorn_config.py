"""
Uvicorn logging configuration to use structured JSON logging.
Configures Uvicorn's access logs and error logs to use our Zap-compatible format.
"""

import logging
from typing import Dict, Any
from src.core.structured_logger import create_component_logger

# Create structured loggers for Uvicorn
uvicorn_log = create_component_logger("UVICORN")
access_log = create_component_logger("ACCESS")

class StructuredUvicornFormatter(logging.Formatter):
    """Custom formatter for Uvicorn logs to use structured JSON format"""
    
    def format(self, record: logging.LogRecord) -> str:
        # Handle different types of Uvicorn messages
        if record.name == "uvicorn.access":
            return self._format_access_log(record)
        else:
            return self._format_server_log(record)
    
    def _format_access_log(self, record: logging.LogRecord) -> str:
        """Format HTTP access logs"""
        message = record.getMessage()
        
        # Parse access log format: "IP:PORT - "METHOD PATH PROTOCOL" STATUS"
        try:
            parts = message.split(' - ')
            if len(parts) >= 2:
                client = parts[0]
                request_parts = parts[1].split('"')
                if len(request_parts) >= 3:
                    request_line = request_parts[1]
                    status_part = request_parts[2].strip()
                    
                    # Parse request line: "METHOD PATH PROTOCOL"
                    request_components = request_line.split(' ')
                    if len(request_components) >= 3:
                        method = request_components[0]
                        path = request_components[1]
                        protocol = request_components[2]
                        status_code = status_part.split()[0] if status_part else "unknown"
                        
                        access_log.with_fields(
                            event_type="http_access",
                            client=client,
                            method=method,
                            path=path,
                            protocol=protocol,
                            status_code=status_code
                        ).info(f"{method} {path} {status_code}")
                        return ""  # Return empty since we've already logged
        except Exception:
            pass
        
        # Fallback for unparseable access logs
        access_log.with_fields(
            event_type="http_access_raw",
            raw_message=message
        ).info(message)
        return ""
    
    def _format_server_log(self, record: logging.LogRecord) -> str:
        """Format server/application logs"""
        message = record.getMessage()
        level = record.levelname.lower()
        
        # Determine event type based on message content
        event_type = "server_info"
        if "started server process" in message.lower():
            event_type = "server_start"
        elif "waiting for application startup" in message.lower():
            event_type = "app_startup"
        elif "application startup complete" in message.lower():
            event_type = "app_ready"
        elif "uvicorn running" in message.lower():
            event_type = "server_ready"
        elif "will watch for changes" in message.lower():
            event_type = "hot_reload"
        
        if level == "info":
            uvicorn_log.with_fields(
                event_type=event_type,
                raw_message=message
            ).info(message)
        elif level == "warning":
            uvicorn_log.with_fields(
                event_type=event_type,
                raw_message=message
            ).warn(message)
        elif level == "error":
            uvicorn_log.with_fields(
                event_type=event_type,
                raw_message=message,
                error=message
            ).error(message)
        else:
            uvicorn_log.with_fields(
                event_type=event_type,
                level=level,
                raw_message=message
            ).info(message)
        
        return ""  # Return empty since we've already logged

def get_uvicorn_log_config() -> Dict[str, Any]:
    """
    Returns Uvicorn logging configuration that uses our structured logging.
    """
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "structured": {
                "()": StructuredUvicornFormatter,
            },
        },
        "handlers": {
            "structured_console": {
                "formatter": "structured",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
            },
        },
        "loggers": {
            "uvicorn": {
                "handlers": ["structured_console"],
                "level": "INFO",
                "propagate": False,
            },
            "uvicorn.error": {
                "handlers": ["structured_console"],
                "level": "INFO",
                "propagate": False,
            },
            "uvicorn.access": {
                "handlers": ["structured_console"],
                "level": "INFO",
                "propagate": False,
            },
        },
    }
