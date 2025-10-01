"""
Key Vault Module for Private Key Encryption/Decryption

This module handles the secure encryption and decryption of user private keys.
It supports two modes of operation:
1. Local development: Master encryption key is sourced from environment variables
2. Production: Master encryption key is managed by AWS KMS
"""

import os
import base64
import json
from typing import Dict, Tuple, Any, Optional, Union
import boto3
from botocore.exceptions import ClientError, NoCredentialsError

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend
import secrets

from src.core.config import settings
from src.core.logging_config import get_core_logger

# Configure logging
logger = get_core_logger()

class KeyVault:
    """
    Key Vault for securely encrypting and decrypting sensitive data like private keys.
    
    In development mode, this uses a master key from environment variables.
    In production, this integrates with AWS KMS service.
    """
    
    def __init__(self):
        self.kms_client = None
        self.using_kms = False
        
        # Initialize KMS client if KMS is configured
        if settings.KMS_PROVIDER == "aws" and settings.KMS_MASTER_KEY_ID:
            try:
                # Initialize AWS KMS client with optional credentials
                kms_kwargs = {"region_name": settings.AWS_REGION}
                
                # Only add credentials if provided (otherwise use IAM role)
                if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
                    kms_kwargs.update({
                        "aws_access_key_id": settings.AWS_ACCESS_KEY_ID,
                        "aws_secret_access_key": settings.AWS_SECRET_ACCESS_KEY
                    })
                    
                    if settings.AWS_SESSION_TOKEN:
                        kms_kwargs["aws_session_token"] = settings.AWS_SESSION_TOKEN
                        
                self.kms_client = boto3.client('kms', **kms_kwargs)
                self.using_kms = True
                logger.info("AWS KMS integration initialized successfully")
            except (ClientError, NoCredentialsError) as e:
                logger.error(f"Failed to initialize AWS KMS client: {e}")
                logger.warning("Falling back to local key management (NOT SECURE FOR PRODUCTION)")
                self.using_kms = False
        
        # Fallback to local master key if KMS is not configured or failed
        if not self.using_kms:
            # Check for master key in environment
            self.master_key = os.getenv("MASTER_ENCRYPTION_KEY")
            
            if not self.master_key:
                logger.warning("MASTER_ENCRYPTION_KEY not found in environment - using fallback from settings (NOT SECURE FOR PRODUCTION)",
                              kms_enabled=self.using_kms,
                              event_type="master_key_fallback")
                # Fallback to settings (only for development)
                self.master_key = settings.JWT_SECRET_KEY
    
    def _generate_data_key(self) -> Tuple[bytes, bytes]:
        """
        Generate a data key using AWS KMS.
        
        This creates a data key that is used to encrypt the actual data.
        The data key itself is encrypted with the KMS master key.
        
        Returns:
            Tuple of (plaintext_key, encrypted_key)
        """
        if not self.using_kms:
            # For local development, generate a random key
            key = Fernet.generate_key()
            # We don't have an encrypted version, so return the same key twice
            return key, key
            
        try:
            # Generate a data key from KMS
            response = self.kms_client.generate_data_key(
                KeyId=settings.KMS_MASTER_KEY_ID,
                KeySpec='AES_256'
            )
            
            # Extract the plaintext and encrypted data key
            plaintext_key = response['Plaintext']
            encrypted_key = response['CiphertextBlob']
            
            return plaintext_key, encrypted_key
        except ClientError as e:
            logger.error("Error generating data key from KMS",
                        error=str(e),
                        kms_key_id=self.kms_client._client_config.__dict__.get('region_name'),
                        event_type="kms_generate_key_error")
            raise
    
    def _decrypt_data_key(self, encrypted_key: bytes) -> bytes:
        """
        Decrypt a data key using AWS KMS.
        
        Args:
            encrypted_key: The encrypted data key
            
        Returns:
            Decrypted data key
        """
        if not self.using_kms:
            # For local development, the "encrypted" key is actually the plaintext key
            return encrypted_key
            
        try:
            # Decrypt the data key using KMS
            response = self.kms_client.decrypt(
                CiphertextBlob=encrypted_key,
                KeyId=settings.KMS_MASTER_KEY_ID
            )
            
            return response['Plaintext']
        except ClientError as e:
            logger.error("Error decrypting data key with KMS",
                        error=str(e),
                        event_type="kms_decrypt_key_error")
            raise
    
    def _derive_key(self, salt: bytes, key_material: bytes) -> bytes:
        """
        Derive an encryption key from key material and salt.
        
        Args:
            salt: Random salt for key derivation
            key_material: Raw key material to derive from
            
        Returns:
            Derived key as bytes
        """
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
            backend=default_backend()
        )
        
        # Derive key
        key = base64.urlsafe_b64encode(kdf.derive(key_material))
        return key
    
    def encrypt(self, data: str) -> Tuple[bytes, Dict[str, Any]]:
        """
        Encrypt data using KMS or local encryption.
        
        Args:
            data: String data to encrypt (e.g., private key)
            
        Returns:
            Tuple of (encrypted_data, metadata)
        """
        # Generate a random salt
        salt = secrets.token_bytes(16)
        
        # Generate or derive a key
        if self.using_kms:
            # Generate a data key from KMS
            plaintext_key, encrypted_key = self._generate_data_key()
            
            # Derive a key using the plaintext data key
            key = self._derive_key(salt, plaintext_key)
            
            # Store the encrypted key in metadata
            key_metadata = {
                "encrypted_key": base64.b64encode(encrypted_key).decode(),
                "kms_key_id": settings.KMS_MASTER_KEY_ID,
                "provider": "aws-kms"
            }
        else:
            # Derive key from master key and salt
            master_key_bytes = self.master_key.encode() if isinstance(self.master_key, str) else self.master_key
            key = self._derive_key(salt, master_key_bytes)
            
            # Store the fact that we're using local encryption
            key_metadata = {
                "provider": "local"
            }
        
        # Create Fernet cipher
        cipher = Fernet(key)
        
        # Encrypt data
        encrypted_data = cipher.encrypt(data.encode())
        
        # Store metadata for decryption
        metadata = {
            "salt": base64.b64encode(salt).decode(),
            "algorithm": "fernet",
            "kdf": "pbkdf2",
            "kdf_iterations": 100000,
            "key": key_metadata
        }
        
        return encrypted_data, metadata
    
    def decrypt(self, encrypted_data: bytes, metadata: Dict[str, Any]) -> str:
        """
        Decrypt data using KMS or local encryption.
        
        Args:
            encrypted_data: Encrypted data as bytes
            metadata: Metadata dictionary containing encryption details
            
        Returns:
            Decrypted data as string
        """
        # Extract metadata
        salt = base64.b64decode(metadata["salt"])
        
        # Verify algorithm
        if metadata.get("algorithm") != "fernet":
            raise ValueError(f"Unsupported encryption algorithm: {metadata.get('algorithm')}")
        
        # Get the key provider from metadata
        key_metadata = metadata.get("key", {})
        provider = key_metadata.get("provider", "local")
        
        # Derive or decrypt the key
        if provider == "aws-kms" and self.using_kms:
            # Decrypt the data key with KMS
            encrypted_key = base64.b64decode(key_metadata["encrypted_key"])
            plaintext_key = self._decrypt_data_key(encrypted_key)
            
            # Derive the key with the plaintext data key
            key = self._derive_key(salt, plaintext_key)
        else:
            # Derive key from master key and salt
            master_key_bytes = self.master_key.encode() if isinstance(self.master_key, str) else self.master_key
            key = self._derive_key(salt, master_key_bytes)
        
        # Create Fernet cipher
        cipher = Fernet(key)
        
        # Decrypt data
        try:
            decrypted_data = cipher.decrypt(encrypted_data)
            return decrypted_data.decode()
        except Exception as e:
            if provider == "aws-kms" and not self.using_kms:
                logger.error("Failed to decrypt KMS-encrypted data with local key - AWS KMS is required",
                           event_type="kms_required_for_decryption")
                raise ValueError("This data was encrypted with AWS KMS and cannot be decrypted locally.") from e
            raise


# Singleton instance
key_vault = KeyVault() 