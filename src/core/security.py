import secrets
import string
import hashlib


# API Key functions
def generate_api_key_prefix() -> str:
    """
    Generate a unique API key prefix.
    
    Returns:
        A string like "sk-abcdef" to use as a prefix for an API key
    """
    chars = string.ascii_letters + string.digits
    random_suffix = ''.join(secrets.choice(chars) for _ in range(6))
    return f"sk-{random_suffix}"

def generate_api_key() -> tuple[str, str]:
    """
    Generate a full API key and its prefix.
    
    Returns:
        Tuple of (full_key, key_prefix)
    """
    key_prefix = generate_api_key_prefix()
    key_secret = secrets.token_hex(32)
    full_key = f"{key_prefix}.{key_secret}"
    return full_key, key_prefix

def get_api_key_hash(api_key: str) -> str:
    """
    Hash an API key for storage using SHA-256.
    
    Fast cryptographic hashing suitable for 256-bit random API keys.
    SHA-256 is secure for API keys with high entropy (unlike passwords).
    """
    return hashlib.sha256(api_key.encode()).hexdigest()

def verify_api_key(plain_api_key: str, hashed_api_key: str) -> bool:
    """
    Verify an API key against a SHA-256 hash.
    
    Fast verification (~0.001ms vs ~500ms with bcrypt).
    Secure for cryptographically random API keys (256 bits entropy).
    """
    computed_hash = hashlib.sha256(plain_api_key.encode()).hexdigest()
    return computed_hash == hashed_api_key
