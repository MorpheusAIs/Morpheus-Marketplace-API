from datetime import datetime, timedelta, timezone
from typing import Any, Union, Optional

from jose import jwt
from passlib.context import CryptContext
from src.core.config import settings

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__truncate_error=False)

# JWT token functions
def create_access_token(subject: Union[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """
    Create a JWT access token for a user.
    
    Args:
        subject: Subject to encode in the token (typically user ID)
        expires_delta: Optional expiration time, defaults to settings value
    
    Returns:
        Encoded JWT token as string
    """
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )
    
    to_encode = {"exp": expire, "sub": str(subject), "type": "access"}
    encoded_jwt = jwt.encode(
        to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM
    )
    return encoded_jwt

def create_refresh_token(subject: Union[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """
    Create a JWT refresh token for a user.
    
    Args:
        subject: Subject to encode in the token (typically user ID)
        expires_delta: Optional expiration time, defaults to settings value
    
    Returns:
        Encoded JWT token as string
    """
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            days=settings.REFRESH_TOKEN_EXPIRE_DAYS
        )
    
    to_encode = {"exp": expire, "sub": str(subject), "type": "refresh"}
    encoded_jwt = jwt.encode(
        to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM
    )
    return encoded_jwt

# Password verification functions
def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a password against a hash.
    
    Args:
        plain_password: Plain text password
        hashed_password: Hashed password to compare against
    
    Returns:
        True if the password matches, False otherwise
    """
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    """
    Hash a password.
    
    Args:
        password: Plain text password to hash
    
    Returns:
        Hashed password
    """
    return pwd_context.hash(password)

# API Key functions
def generate_api_key_prefix() -> str:
    """
    Generate a unique API key prefix.
    
    Returns:
        A string like "sk-abcdef" to use as a prefix for an API key
    """
    import secrets
    import string
    
    # Generate 6 random alphanumeric characters
    chars = string.ascii_letters + string.digits
    random_suffix = ''.join(secrets.choice(chars) for _ in range(6))
    
    # Return with "sk-" prefix
    return f"sk-{random_suffix}"

def generate_api_key() -> tuple[str, str]:
    """
    Generate a full API key and its prefix.
    
    Returns:
        Tuple of (full_key, key_prefix)
    """
    import secrets
    
    # Generate prefix like "sk-abcdef"
    key_prefix = generate_api_key_prefix()
    
    # Generate a secure random key (32 bytes = 256 bits)
    key_secret = secrets.token_hex(32)
    
    # Combine to create the full key
    full_key = f"{key_prefix}.{key_secret}"
    
    return full_key, key_prefix

def get_api_key_hash(api_key: str) -> str:
    """
    Hash an API key for storage.
    
    Args:
        api_key: Full API key
    
    Returns:
        Hashed API key
    """
    truncated_api_key = api_key[3:]
    return pwd_context.hash(truncated_api_key)

def verify_api_key(plain_api_key: str, hashed_api_key: str) -> bool:
    """
    Verify an API key against a hash.
    
    Args:
        plain_api_key: Plain text API key
        hashed_api_key: Hashed API key to compare against
    
    Returns:
        True if the API key matches, False otherwise
    """
    truncated_api_key = plain_api_key[3:]
    return pwd_context.verify(truncated_api_key, hashed_api_key) 