"""
Wallet Service for Web3 wallet integration.

Supports:
- EIP-191: Personal sign for EOA wallets
- EIP-1271: Contract signature verification for smart wallets (optional, requires Web3 provider)
- ERC-4361: Sign-In with Ethereum (SIWE) using the official siwe-py library

Uses the official siwe-py library: https://github.com/spruceid/siwe-py
"""
from enum import Enum
from dataclasses import dataclass
from typing import Optional, Tuple
from datetime import datetime, timezone

from eth_account.messages import encode_defunct
from eth_account import Account
from web3 import Web3
from siwe import SiweMessage

from ..core.logging_config import get_auth_logger
from ..core.config import settings

logger = get_auth_logger()


# =============================================================================
# Types and Constants
# =============================================================================

class WalletType(str, Enum):
    """Type of wallet that was verified."""
    EOA = "eoa"  # Externally Owned Account (MetaMask, etc.)
    CONTRACT = "contract"  # Smart contract wallet (Safe, Argent, etc.)
    UNKNOWN = "unknown"


@dataclass
class VerificationResult:
    """Result of signature verification."""
    is_valid: bool
    wallet_type: WalletType
    recovered_address: Optional[str] = None
    error: Optional[str] = None
    
    def to_tuple(self) -> Tuple[bool, Optional[str]]:
        """Convert to tuple format for backward compatibility."""
        if self.is_valid:
            return True, self.recovered_address
        return False, self.error


# EIP-1271 constants
EIP1271_MAGIC_VALUE = bytes.fromhex("1626ba7e")
EIP1271_ABI = [
    {
        "inputs": [
            {"name": "_hash", "type": "bytes32"},
            {"name": "_signature", "type": "bytes"}
        ],
        "name": "isValidSignature",
        "outputs": [{"name": "magicValue", "type": "bytes4"}],
        "stateMutability": "view",
        "type": "function"
    }
]


# =============================================================================
# SIWE Helper (ERC-4361) using official siwe-py library
# =============================================================================

def create_siwe_message(
    address: str,
    nonce: str,
    issued_at: Optional[str] = None,
    statement: Optional[str] = None,
    expiration_time: Optional[str] = None,
    domain: Optional[str] = None,
    uri: Optional[str] = None,
    chain_id: Optional[int] = None,
    resources: Optional[list] = None
) -> SiweMessage:
    """
    Create a SIWE message using the official siwe-py library.
    
    Args:
        address: Ethereum address (checksummed)
        nonce: Random nonce for replay protection
        issued_at: ISO 8601 timestamp (defaults to now)
        statement: Human-readable statement
        expiration_time: Optional expiration timestamp
        domain: Domain requesting the signature (defaults to settings.SIWE_DOMAIN)
        uri: URI of the application (defaults to settings.SIWE_URI)
        chain_id: Chain ID (defaults to settings.SIWE_CHAIN_ID)
        resources: Optional list of resource URIs
        
    Returns:
        SiweMessage instance
    """
    if issued_at is None:
        issued_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    message = SiweMessage(
        domain=domain or settings.SIWE_DOMAIN,
        address=Web3.to_checksum_address(address),
        statement=statement or "Link your wallet to your Morpheus API Gateway account.",
        uri=uri or settings.SIWE_URI,
        version="1",
        chain_id=chain_id or settings.SIWE_CHAIN_ID,
        nonce=nonce,
        issued_at=issued_at,
        expiration_time=expiration_time,
        resources=resources
    )
    
    return message


def get_siwe_message_template() -> str:
    """
    Get the SIWE message template for frontend use.
    
    Returns a template string with placeholders for dynamic values.
    """
    return f"""{settings.SIWE_DOMAIN} wants you to sign in with your Ethereum account:
{{wallet_address}}

Link your wallet to your Morpheus API Gateway account.

URI: {settings.SIWE_URI}
Version: 1
Chain ID: {settings.SIWE_CHAIN_ID}
Nonce: {{nonce}}
Issued At: {{timestamp}}"""


# =============================================================================
# Wallet Verification Service
# =============================================================================

class WalletVerificationService:
    """
    Handles Ethereum signature verification for both EOA and smart contract wallets.
    
    Features:
    - EIP-191: Always available for EOA wallets
    - EIP-1271: Available when Web3 provider is configured
    - ERC-4361: SIWE message verification using official siwe-py library
    - Automatic wallet type detection and fallback
    """
    
    def __init__(self, web3_provider_url: Optional[str] = None):
        """
        Initialize the verification service.
        
        Args:
            web3_provider_url: Optional Web3 provider URL for EIP-1271 support.
                              If not provided, uses settings.WEB3_PROVIDER_URL.
                              If neither available, only EOA verification is supported.
        """
        self._web3: Optional[Web3] = None
        self._web3_provider_url = web3_provider_url or settings.WEB3_PROVIDER_URL
        self._web3_initialized = False
    
    @property
    def web3(self) -> Optional[Web3]:
        """Lazy initialization of Web3 instance."""
        if not self._web3_initialized:
            self._web3_initialized = True
            if self._web3_provider_url:
                try:
                    self._web3 = Web3(Web3.HTTPProvider(self._web3_provider_url))
                    if self._web3.is_connected():
                        logger.info(
                            "Web3 provider connected",
                            provider_url=self._web3_provider_url[:50] + "...",
                            event_type="web3_connected"
                        )
                    else:
                        logger.warning(
                            "Web3 provider not connected",
                            event_type="web3_connection_failed"
                        )
                        self._web3 = None
                except Exception as e:
                    logger.warning(
                        "Failed to initialize Web3 provider",
                        error=str(e),
                        event_type="web3_init_error"
                    )
                    self._web3 = None
        return self._web3
    
    @property
    def supports_contract_wallets(self) -> bool:
        """Check if EIP-1271 verification is available."""
        return self.web3 is not None
    
    def verify_signature(
        self,
        wallet_address: str,
        message: str,
        signature: str
    ) -> VerificationResult:
        """
        Verify that the signature was created by the wallet owner.
        
        Tries EOA verification first (EIP-191), then falls back to
        smart contract verification (EIP-1271) if available.
        
        Args:
            wallet_address: Expected wallet address that signed the message
            message: The message that was signed
            signature: The signature to verify
            
        Returns:
            VerificationResult with validation status and wallet type
        """
        try:
            checksummed_address = Web3.to_checksum_address(wallet_address)
        except Exception as e:
            return VerificationResult(
                is_valid=False,
                wallet_type=WalletType.UNKNOWN,
                error=f"Invalid wallet address: {e}"
            )
        
        # Try EOA verification first (EIP-191)
        eoa_result = self._verify_eoa_signature(checksummed_address, message, signature)
        if eoa_result.is_valid:
            return eoa_result
        
        # If EOA failed and we have Web3, check if it's a contract wallet
        if self.web3 and self._is_contract(checksummed_address):
            logger.info(
                "EOA verification failed, trying EIP-1271 contract verification",
                wallet_address=checksummed_address,
                event_type="trying_eip1271"
            )
            return self._verify_eip1271_signature(checksummed_address, message, signature)
        
        # Return the EOA result (which failed)
        return eoa_result
    
    def _verify_eoa_signature(
        self,
        wallet_address: str,
        message: str,
        signature: str
    ) -> VerificationResult:
        """Verify signature using EIP-191 (personal_sign)."""
        try:
            # Encode the message as per EIP-191
            message_hash = encode_defunct(text=message)
            
            # Recover the address that signed the message
            recovered_address = Account.recover_message(
                message_hash,
                signature=signature
            )
            
            # Compare addresses (case-insensitive)
            recovered_checksummed = Web3.to_checksum_address(recovered_address)
            
            if wallet_address == recovered_checksummed:
                logger.info(
                    "EOA signature verified successfully",
                    wallet_address=wallet_address,
                    event_type="eoa_signature_verified"
                )
                return VerificationResult(
                    is_valid=True,
                    wallet_type=WalletType.EOA,
                    recovered_address=recovered_checksummed
                )
            
            return VerificationResult(
                is_valid=False,
                wallet_type=WalletType.EOA,
                error=f"Address mismatch: recovered {recovered_checksummed}, expected {wallet_address}"
            )
            
        except Exception as e:
            return VerificationResult(
                is_valid=False,
                wallet_type=WalletType.UNKNOWN,
                error=str(e)
            )
    
    def _verify_eip1271_signature(
        self,
        contract_address: str,
        message: str,
        signature: str
    ) -> VerificationResult:
        """
        Verify signature using EIP-1271 (smart contract wallet).
        
        Calls the contract's isValidSignature(bytes32 hash, bytes signature) method.
        """
        if not self.web3:
            return VerificationResult(
                is_valid=False,
                wallet_type=WalletType.CONTRACT,
                error="Web3 provider not configured for contract wallet verification"
            )
        
        try:
            # Create the EIP-191 prefixed hash (what the contract expects)
            message_bytes = message.encode('utf-8')
            prefix = f"\x19Ethereum Signed Message:\n{len(message_bytes)}"
            hash_bytes = self.web3.keccak(prefix.encode('utf-8') + message_bytes)
            
            # Create contract instance
            contract = self.web3.eth.contract(
                address=contract_address,
                abi=EIP1271_ABI
            )
            
            # Parse signature
            sig_bytes = bytes.fromhex(
                signature[2:] if signature.startswith('0x') else signature
            )
            
            # Call isValidSignature
            result = contract.functions.isValidSignature(
                hash_bytes,
                sig_bytes
            ).call()
            
            if result == EIP1271_MAGIC_VALUE:
                logger.info(
                    "EIP-1271 contract signature verified successfully",
                    wallet_address=contract_address,
                    event_type="eip1271_signature_verified"
                )
                return VerificationResult(
                    is_valid=True,
                    wallet_type=WalletType.CONTRACT,
                    recovered_address=contract_address
                )
            
            logger.warning(
                "EIP-1271 signature verification failed",
                wallet_address=contract_address,
                magic_value_returned=result.hex() if result else None,
                event_type="eip1271_invalid_signature"
            )
            return VerificationResult(
                is_valid=False,
                wallet_type=WalletType.CONTRACT,
                error="Contract returned invalid magic value"
            )
            
        except Exception as e:
            logger.warning(
                "EIP-1271 verification error",
                wallet_address=contract_address,
                error=str(e),
                event_type="eip1271_verification_error"
            )
            return VerificationResult(
                is_valid=False,
                wallet_type=WalletType.CONTRACT,
                error=f"Contract verification failed: {e}"
            )
    
    def _is_contract(self, address: str) -> bool:
        """Check if an address is a smart contract."""
        if not self.web3:
            return False
        try:
            code = self.web3.eth.get_code(address)
            return len(code) > 0
        except Exception as e:
            logger.warning(
                "Error checking if address is a smart contract",
                address=address,
                error=str(e),
                event_type="contract_check_error"
            )
            return False
    
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


# =============================================================================
# Wallet Linking Service
# =============================================================================

class WalletLinkingService:
    """High-level service for wallet linking operations using SIWE."""
    
    def __init__(self, web3_provider_url: Optional[str] = None):
        self.verification_service = WalletVerificationService(web3_provider_url)
    
    def get_siwe_message_template(self) -> str:
        """Get the SIWE message template for frontend use."""
        return get_siwe_message_template()
    
    def create_siwe_message(
        self,
        wallet_address: str,
        nonce: str,
        timestamp: Optional[str] = None,
        statement: Optional[str] = None
    ) -> str:
        """
        Create a SIWE-compliant message for signing.
        
        Args:
            wallet_address: The wallet address
            nonce: Unique nonce for this request
            timestamp: Optional ISO 8601 timestamp (defaults to now)
            statement: Optional human-readable statement
            
        Returns:
            SIWE-formatted message string
        """
        siwe_msg = create_siwe_message(
            address=wallet_address,
            nonce=nonce,
            issued_at=timestamp,
            statement=statement
        )
        return siwe_msg.prepare_message()
    
    def validate_message_format(
        self,
        message: str,
        wallet_address: str,
        nonce: str,
        timestamp: str
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate that the signed message matches the expected SIWE format.
        
        Args:
            message: The message that was signed
            wallet_address: The wallet address from the request
            nonce: The nonce from the request
            timestamp: The timestamp from the request
            
        Returns:
            Tuple of (is_valid, error_message if invalid)
        """
        try:
            # Parse the message using siwe-py
            siwe_msg = SiweMessage.from_message(message=message)
            
            # Validate the parsed values match expected
            checksummed = Web3.to_checksum_address(wallet_address)
            
            if siwe_msg.address != checksummed:
                return False, f"Address mismatch: expected {checksummed}, got {siwe_msg.address}"
            
            if siwe_msg.nonce != nonce:
                return False, f"Nonce mismatch: expected {nonce}, got {siwe_msg.nonce}"
            
            if siwe_msg.domain != settings.SIWE_DOMAIN:
                return False, f"Domain mismatch: expected {settings.SIWE_DOMAIN}, got {siwe_msg.domain}"
            
            return True, None
            
        except Exception as e:
            logger.warning(
                "SIWE message parsing failed",
                error=str(e),
                event_type="siwe_parse_error"
            )
            return False, f"Invalid SIWE message format: {e}"
    
    def verify_wallet_ownership(
        self,
        wallet_address: str,
        message: str,
        signature: str
    ) -> Tuple[bool, Optional[str]]:
        """
        Verify that the user owns the wallet by checking the signature.
        
        Supports both EOA wallets (EIP-191) and smart contract wallets (EIP-1271).
        
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
        
        # Verify the signature (tries EOA first, then contract wallet if available)
        verification_result = self.verification_service.verify_signature(
            wallet_address=checksummed_address,
            message=message,
            signature=signature
        )
        
        return verification_result.to_tuple()
    
    def verify_wallet_ownership_extended(
        self,
        wallet_address: str,
        message: str,
        signature: str
    ) -> VerificationResult:
        """
        Verify wallet ownership with extended result information.
        
        Returns a VerificationResult with wallet type information.
        """
        is_valid, result = self.verification_service.validate_wallet_address(wallet_address)
        if not is_valid:
            return VerificationResult(
                is_valid=False,
                wallet_type=WalletType.UNKNOWN,
                error=result
            )
        
        return self.verification_service.verify_signature(
            wallet_address=result,
            message=message,
            signature=signature
        )


# =============================================================================
# Global Service Instances
# =============================================================================

# Global instances (initialized on first import)
# These use lazy Web3 initialization internally, so no connection is made until needed
wallet_verification_service = WalletVerificationService()
wallet_linking_service = WalletLinkingService()
