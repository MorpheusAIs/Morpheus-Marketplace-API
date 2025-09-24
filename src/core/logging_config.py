"""
Zap-compatible logging configuration for Morpheus Marketplace API.
Aligns with Morpheus-Lumerin-Node logging patterns and structure.
"""

import json
import logging
import sys
import os
from datetime import datetime
from typing import Any, Dict, Optional


class ZapCompatibleJSONFormatter(logging.Formatter):
    """JSON formatter compatible with Zap logger structure from Morpheus-Lumerin-Node"""
    
    def format(self, record: logging.LogRecord) -> str:
        # Zap-compatible structure (match proxy-router format with uppercase levels)
        log_entry = {
            "level": record.levelname.upper(),  # match proxy-router uppercase levels
            "ts": datetime.utcnow().isoformat() + "Z",  # zap timestamp format
            "caller": f"{record.module}:{record.lineno}",
            "logger": record.name,
            "msg": record.getMessage()
        }
        
        # Add structured fields (similar to zap's .With())
        if hasattr(record, 'structured_fields'):
            log_entry.update(record.structured_fields)
            
        # Add exception info if present
        if record.exc_info:
            log_entry["stacktrace"] = self.formatException(record.exc_info)
            
        return json.dumps(log_entry, default=str)


class ZapCompatibleConsoleFormatter(logging.Formatter):
    """Console formatter that mimics Zap's development mode output"""
    
    def format(self, record: logging.LogRecord) -> str:
        # Zap-style console format: timestamp + level + logger + message
        timestamp = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%fZ')
        level = record.levelname.upper()
        logger_name = record.name
        message = record.getMessage()
        
        # Add structured fields if present
        if hasattr(record, 'structured_fields') and record.structured_fields:
            fields_str = " ".join([f"{k}={v}" for k, v in record.structured_fields.items()])
            message = f"{message} {fields_str}"
        
        return f"{timestamp}\t{level}\t{logger_name}\t{message}"


def setup_zap_compatible_logging():
    """
    Configure Zap-compatible structured logging.
    Uses environment variables matching Morpheus-Lumerin-Node patterns.
    """
    
    # Environment variables (matching Lumerin Node)
    use_json = os.getenv('LOG_JSON', 'true').lower() == 'true'
    log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
    log_color = os.getenv('LOG_COLOR', 'false').lower() == 'true'
    log_is_prod = os.getenv('LOG_IS_PROD', 'false').lower() == 'true'
    
    # Get root logger and configure
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level, logging.INFO))
    
    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Add handler with appropriate formatter
    handler = logging.StreamHandler(sys.stdout)
    
    if use_json:
        handler.setFormatter(ZapCompatibleJSONFormatter())
    else:
        handler.setFormatter(ZapCompatibleConsoleFormatter())
    
    root_logger.addHandler(handler)
    
    # Log the configuration
    config_logger = logging.getLogger("LOGGING_CONFIG")
    config_logger.info(f"Zap-compatible logging initialized: JSON={use_json}, Level={log_level}, Prod={log_is_prod}")
    
    return root_logger


def get_component_log_level(component: str) -> str:
    """Get component-specific log level (like Lumerin Node)"""
    env_var = f"LOG_LEVEL_{component.upper()}"
    return os.getenv(env_var, os.getenv('LOG_LEVEL', 'INFO')).upper()
