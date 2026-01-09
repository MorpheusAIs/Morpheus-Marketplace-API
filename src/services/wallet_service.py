"""
Wallet Service for Web3 wallet integration.

Handles signature verification using EIP-191 personal_sign standard.
"""
import logging
from typing import Optional, Tuple

from eth_account.messages import encode_defunct
from eth_account import Account
from web3 import Web3

from ..core.logging_config import get_auth_logger
from ..schemas.wallet import create_sign_message

logger = get_auth_logger()


class WalletVerificationService:
    """Handles Ethereum signature verification."""
    
    @staticmethod
    def verify_signature(
        wallet_address: str,
        message: str,
        signature: str
    ) -> Tuple[bool, Optional[str]]:
        """
        Verify that the signature was created by the wallet owner.
        
        Uses EIP-191 personal_sign standard (what MetaMask/WalletConnect use).
        
        Args:
            wallet_address: Expected wallet address that signed the message
            message: The message that was signed
            signature: The signature to verify
            
        Returns:
            Tuple of (is_valid, recovered_address or error_message)
        """
        try:
            # Encode the message as per EIP-191
            message_hash = encode_defunct(text=message)
            
            # Recover the address that signed the message
            recovered_address = Account.recover_message(
                message_hash,
                signature=signature
            )
            
            # Compare addresses (case-insensitive)
            expected_checksummed = Web3.to_checksum_address(wallet_address)
            recovered_checksummed = Web3.to_checksum_address(recovered_address)
            
            is_valid = expected_checksummed == recovered_checksummed
            
            if not is_valid:
                logger.warning(
                    "Signature verification failed: address mismatch",
                    expected_address=expected_checksummed,
                    recovered_address=recovered_checksummed,
                    event_type="signature_mismatch"
                )
                return False, f"Recovered address {recovered_checksummed} does not match expected {expected_checksummed}"
            
            logger.info(
                "Signature verified successfully",
                wallet_address=expected_checksummed,
                event_type="signature_verified"
            )
            return True, recovered_checksummed
            
        except Exception as e:
            logger.warning(
                "Signature verification failed",
                error=str(e),
                event_type="signature_verification_error"
            )
            return False, str(e)
    
    @staticmethod
    def validate_wallet_address(address: str) -> Tuple[bool, Optional[str]]:
        """
        Validate Ethereum address format and return checksummed version.
        
        Args:
            address: Wallet address to validate
            
        Returns:
            Tuple of (is_valid, checksummed_address or error_message)
        """
        if not address:
            return False, "Address cannot be empty"
        
        if not address.startswith("0x"):
            return False, "Address must start with 0x"
        
        if len(address) != 42:
            return False, "Address must be 42 characters (0x + 40 hex)"
        
        try:
            checksummed = Web3.to_checksum_address(address)
            return True, checksummed
        except ValueError as e:
            return False, f"Invalid address: {str(e)}"
    
    @staticmethod
    def to_checksum_address(address: str) -> str:
        """Convert an address to checksummed format."""
        return Web3.to_checksum_address(address)
    
    @staticmethod
    def normalize_address(address: str) -> str:
        """Normalize an address to lowercase for storage."""
        return address.lower()


class WalletLinkingService:
    """High-level service for wallet linking operations."""
    
    def __init__(self):
        self.verification_service = WalletVerificationService()
    
    def validate_message_format(
        self,
        message: str,
        wallet_address: str,
        nonce: str,
        timestamp: str
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate that the signed message matches the expected format.
        
        This prevents users from submitting arbitrary signed messages.
        
        Args:
            message: The message that was signed
            wallet_address: The wallet address from the request
            nonce: The nonce from the request
            timestamp: The timestamp from the request
            
        Returns:
            Tuple of (is_valid, error_message if invalid)
        """
        expected_message = create_sign_message(
            wallet_address=wallet_address,
            nonce=nonce,
            timestamp=timestamp
        )
        
        if message.strip() != expected_message.strip():
            logger.warning(
                "Message format mismatch",
                event_type="message_format_mismatch"
            )
            return False, "Message format does not match expected template"
        
        return True, None
    
    def verify_wallet_ownership(
        self,
        wallet_address: str,
        message: str,
        signature: str
    ) -> Tuple[bool, Optional[str]]:
        """
        Verify that the user owns the wallet by checking the signature.
        
        Args:
            wallet_address: Claimed wallet address
            message: The message that was signed
            signature: The signature to verify
            
        Returns:
            Tuple of (is_valid, checksummed_address or error_message)
        """
        # First validate the address format
        is_valid, result = self.verification_service.validate_wallet_address(wallet_address)
        if not is_valid:
            return False, result
        
        checksummed_address = result
        
        # Then verify the signature
        return self.verification_service.verify_signature(
            wallet_address=checksummed_address,
            message=message,
            signature=signature
        )


# Global service instances
wallet_verification_service = WalletVerificationService()
wallet_linking_service = WalletLinkingService()

