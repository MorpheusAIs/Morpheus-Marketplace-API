"""
Version configuration for Morpheus Marketplace API.

This module provides build-time version information that gets injected
during the Docker build process, similar to the Node repository pattern.
"""

import os
from datetime import datetime

# These values will be set at build time via environment variables
# Default values are used for development
BUILD_VERSION = os.getenv("BUILD_VERSION", "0.0.0-dev")
BUILD_COMMIT = os.getenv("BUILD_COMMIT", "unknown")
BUILD_TIME = os.getenv("BUILD_TIME", datetime.now().isoformat())

def get_version_info():
    """Get comprehensive version information."""
    return {
        "version": BUILD_VERSION,
        "commit": BUILD_COMMIT,
        "build_time": BUILD_TIME,
    }

def get_version():
    """Get just the version string."""
    return BUILD_VERSION
