"""
API Key Encryption Module

Provides secure encryption/decryption of API keys using Cognito user data.
"""

import base64
import hashlib
from typing import Optional
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend
import secrets
from src.core.config import settings

from src.core.logging_config import get_auth_logger

logger = get_auth_logger()

class APIKeyEncryption:
    """Handle encryption and decryption of API keys using Cognito user data."""
    
    ENCRYPTION_VERSION = 1
    KEY_LENGTH = 32  # 256-bit key
    IV_LENGTH = 16   # 128-bit IV for AES
    PBKDF2_ITERATIONS = 100000  # High iteration count for security
    
    @classmethod
    def derive_encryption_key(cls, cognito_user_id: str, user_email: str) -> bytes:
        """
        Derive encryption key from Cognito user data.
        
        Args:
            cognito_user_id: Cognito sub claim (permanent user ID)
            user_email: User's email address
            
        Returns:
            32-byte encryption key
        """
        # Combine stable user identifiers with server secret
        key_material = f"{cognito_user_id}:{user_email}:{settings.ENCRYPTION_SECRET_KEY}"
        
        # Use Cognito user ID as salt (it's unique and permanent)
        salt = cognito_user_id.encode('utf-8')
        
        # Derive key using PBKDF2
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=cls.KEY_LENGTH,
            salt=salt,
            iterations=cls.PBKDF2_ITERATIONS,
            backend=default_backend()
        )
        
        return kdf.derive(key_material.encode('utf-8'))
    
    @classmethod
    def encrypt_api_key(cls, api_key: str, cognito_user_id: str, user_email: str) -> str:
        """
        Encrypt an API key for secure storage.
        
        Args:
            api_key: Full API key to encrypt
            cognito_user_id: Cognito sub claim
            user_email: User's email
            
        Returns:
            Base64 encoded encrypted data (IV + encrypted_key)
        """
        # Derive encryption key
        encryption_key = cls.derive_encryption_key(cognito_user_id, user_email)
        
        # Generate random IV
        iv = secrets.token_bytes(cls.IV_LENGTH)
        
        # Encrypt the API key
        cipher = Cipher(
            algorithms.AES(encryption_key),
            modes.CBC(iv),
            backend=default_backend()
        )
        encryptor = cipher.encryptor()
        
        # Pad the API key to AES block size (16 bytes)
        padded_key = cls._pad_data(api_key.encode('utf-8'))
        encrypted_key = encryptor.update(padded_key) + encryptor.finalize()
        
        # Combine IV + encrypted data and encode as base64
        combined_data = iv + encrypted_key
        return base64.b64encode(combined_data).decode('utf-8')
    
    @classmethod
    def decrypt_api_key(cls, encrypted_data: str, cognito_user_id: str, user_email: str) -> Optional[str]:
        """
        Decrypt an API key from storage.
        
        Args:
            encrypted_data: Base64 encoded encrypted data
            cognito_user_id: Cognito sub claim
            user_email: User's email
            
        Returns:
            Decrypted API key or None if decryption fails
        """
        try:
            # Decode base64 data
            combined_data = base64.b64decode(encrypted_data.encode('utf-8'))
            
            # Extract IV and encrypted key
            iv = combined_data[:cls.IV_LENGTH]
            encrypted_key = combined_data[cls.IV_LENGTH:]
            
            # Derive encryption key
            encryption_key = cls.derive_encryption_key(cognito_user_id, user_email)
            
            # Decrypt the API key
            cipher = Cipher(
                algorithms.AES(encryption_key),
                modes.CBC(iv),
                backend=default_backend()
            )
            decryptor = cipher.decryptor()
            
            padded_key = decryptor.update(encrypted_key) + decryptor.finalize()
            api_key = cls._unpad_data(padded_key).decode('utf-8')
            
            return api_key
            
        except Exception as e:
            # Log error but don't expose details
            logger.error(f"Decryption failed: {e}")
            return None
    
    @staticmethod
    def _pad_data(data: bytes) -> bytes:
        """Add PKCS7 padding to data."""
        padding_length = 16 - (len(data) % 16)
        padding = bytes([padding_length] * padding_length)
        return data + padding
    
    @staticmethod
    def _unpad_data(padded_data: bytes) -> bytes:
        """Remove PKCS7 padding from data."""
        padding_length = padded_data[-1]
        return padded_data[:-padding_length]
