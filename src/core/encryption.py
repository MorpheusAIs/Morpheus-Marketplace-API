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
    def derive_encryption_key(cls, cognito_user_id: str) -> bytes:
        """
        Derive encryption key from Cognito user ID.
        
        Args:
            cognito_user_id: Cognito sub claim (permanent user ID)
            
        Returns:
            32-byte encryption key
        """
        # Combine stable user identifier with server secret
        key_material = f"{cognito_user_id}:{settings.ENCRYPTION_SECRET_KEY}"
        
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
    def encrypt_api_key(cls, api_key: str, cognito_user_id: str) -> str:
        """
        Encrypt an API key for secure storage.
        
        Args:
            api_key: Full API key to encrypt
            cognito_user_id: Cognito sub claim
            
        Returns:
            Base64 encoded encrypted data (IV + encrypted_key)
        """
        # Derive encryption key
        encryption_key = cls.derive_encryption_key(cognito_user_id)
        
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
    def decrypt_api_key(cls, encrypted_data: str, cognito_user_id: str) -> Optional[str]:
        """
        Decrypt an API key from storage.
        
        Args:
            encrypted_data: Base64 encoded encrypted data
            cognito_user_id: Cognito sub claim
            
        Returns:
            Decrypted API key or None if decryption fails
        """
        try:
            logger.debug("Starting API key decryption", 
                        cognito_user_id=cognito_user_id[:8] + "...",  # Log partial ID for privacy
                        encrypted_data_length=len(encrypted_data),
                        event_type="api_key_decryption_start")
            
            # Decode base64 data
            combined_data = base64.b64decode(encrypted_data.encode('utf-8'))
            logger.debug("Base64 decoding successful", 
                        combined_data_length=len(combined_data),
                        event_type="base64_decode_success")
            
            # Extract IV and encrypted key
            iv = combined_data[:cls.IV_LENGTH]
            encrypted_key = combined_data[cls.IV_LENGTH:]
            logger.debug("IV and encrypted key extracted", 
                        iv_length=len(iv),
                        encrypted_key_length=len(encrypted_key),
                        event_type="iv_extraction_success")
            
            # Derive encryption key
            encryption_key = cls.derive_encryption_key(cognito_user_id)
            logger.debug("Encryption key derived", 
                        encryption_key_length=len(encryption_key),
                        event_type="key_derivation_success")
            
            # Decrypt the API key
            cipher = Cipher(
                algorithms.AES(encryption_key),
                modes.CBC(iv),
                backend=default_backend()
            )
            decryptor = cipher.decryptor()
            
            padded_key = decryptor.update(encrypted_key) + decryptor.finalize()
            logger.debug("AES decryption successful", 
                        padded_key_length=len(padded_key),
                        event_type="aes_decryption_success")
            
            api_key = cls._unpad_data(padded_key).decode('utf-8')
            logger.debug("API key decryption completed successfully", 
                        api_key_prefix=api_key[:10] + "..." if len(api_key) > 10 else api_key,
                        api_key_length=len(api_key),
                        event_type="api_key_decryption_success")
            
            return api_key
            
        except Exception as e:
            # Log detailed error information for debugging
            logger.error("API key decryption failed", 
                        cognito_user_id=cognito_user_id[:8] + "...",  # Log partial ID for privacy
                        encrypted_data_length=len(encrypted_data) if encrypted_data else 0,
                        error_type=type(e).__name__,
                        error_message=str(e),
                        event_type="api_key_decryption_error")
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
