import os
from typing import List, Union, Optional, Any
from pydantic_settings import BaseSettings
from pydantic import PostgresDsn, Field, AnyHttpUrl, field_validator
from dotenv import load_dotenv

# Load .env file variables
load_dotenv()

class Settings(BaseSettings):
    # Project Settings
    PROJECT_NAME: str = "Morpheus API Gateway"
    API_V1_STR: str = "/api/v1"
    
    # Base URL - set by Terraform based on environment
    BASE_URL: str = Field(default=os.getenv("BASE_URL", "http://localhost:8000"))
    
    # CORS Settings - default to an empty list that allows all origins
    BACKEND_CORS_ORIGINS: Union[List[str], str] = Field(default="*")
    
    @field_validator("BACKEND_CORS_ORIGINS", mode="before")
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> Union[List[str], str]:
        if isinstance(v, str) and not v.startswith("["):
            if v == "":
                # Return ["*"] to allow all origins if empty string
                return ["*"]
            return [i.strip() for i in v.split(",") if i.strip()]
        elif isinstance(v, list):
            return v
        return v
    
    # Database Connection - Using default port 5432 to match running Docker container
    DATABASE_URL: str = Field(default=os.getenv("DATABASE_URL"))
    
    # Database Settings (placeholders for Docker)
    DB_USER: str = Field(default=os.getenv("POSTGRES_USER", "morpheus_user"))
    DB_PASSWORD: str = Field(default=os.getenv("POSTGRES_PASSWORD", "secure_password_here"))
    DB_NAME: str = Field(default=os.getenv("POSTGRES_DB", "morpheus_db"))

    # JWT Settings
    JWT_SECRET_KEY: str = Field(default=os.getenv("JWT_SECRET_KEY", "super_secret_key_change_me"))
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
    REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))
    
    # API Key Encryption
    ENCRYPTION_SECRET_KEY: str = Field(default=os.getenv("ENCRYPTION_SECRET_KEY", "encryption_secret_change_me"))

    # Proxy Router Settings
    PROXY_ROUTER_URL: str = Field(default=os.getenv("PROXY_ROUTER_URL", ""))
    PROXY_ROUTER_USERNAME: str = Field(default=os.getenv("PROXY_ROUTER_USERNAME", ""))
    PROXY_ROUTER_PASSWORD: str = Field(default=os.getenv("PROXY_ROUTER_PASSWORD", ""))
    
    # Blockchain Private Key Fallback
    FALLBACK_PRIVATE_KEY: str | None = Field(default=os.getenv("FALLBACK_PRIVATE_KEY"))

    # KMS Settings (placeholders - specific config depends on KMS choice)
    KMS_PROVIDER: str | None = Field(default=os.getenv("KMS_PROVIDER", "aws"))
    KMS_MASTER_KEY_ID: str | None = Field(default=os.getenv("KMS_MASTER_KEY_ID"))
    
    # AWS KMS specific settings
    AWS_REGION: str = os.getenv("AWS_REGION", "us-east-2")
    AWS_ACCESS_KEY_ID: str | None = Field(default=os.getenv("AWS_ACCESS_KEY_ID"))
    AWS_SECRET_ACCESS_KEY: str | None = Field(default=os.getenv("AWS_SECRET_ACCESS_KEY"))
    AWS_SESSION_TOKEN: str | None = Field(default=os.getenv("AWS_SESSION_TOKEN"))
    
    # AWS Cognito Settings
    COGNITO_USER_POOL_ID: str = Field(default=os.getenv("COGNITO_USER_POOL_ID", "us-east-2_tqCTHoSST"))
    COGNITO_CLIENT_ID: str = Field(default=os.getenv("COGNITO_CLIENT_ID", "7faqqo5lcj3175epjqs2upvmmu"))
    COGNITO_REGION: str = Field(default=os.getenv("COGNITO_REGION", "us-east-2"))
    COGNITO_DOMAIN: str = Field(default=os.getenv("COGNITO_DOMAIN", "auth.mor.org"))
    COGNITO_JWKS_URL: str = Field(default=f"https://cognito-idp.{os.getenv('COGNITO_REGION', 'us-east-2')}.amazonaws.com/{os.getenv('COGNITO_USER_POOL_ID', 'us-east-2_tqCTHoSST')}/.well-known/jwks.json")
    
    # Local encryption key (for development)
    MASTER_ENCRYPTION_KEY: str | None = Field(default=os.getenv("MASTER_ENCRYPTION_KEY"))
    
    # Automation feature flag
    AUTOMATION_FEATURE_ENABLED: bool = Field(default=os.getenv("AUTOMATION_FEATURE_ENABLED", "False").lower() == "true")
    
    # Delegation
    GATEWAY_DELEGATE_ADDRESS: str = "0xGatewayDelegateAccountAddressPlaceholder" # Placeholder
    
    # Direct Model Fetching Settings (replaces model sync)
    ACTIVE_MODELS_URL: str = Field(default=os.getenv("ACTIVE_MODELS_URL", "https://active.dev.mor.org/active_models.json"))
    DEFAULT_FALLBACK_MODEL: str = Field(default=os.getenv("DEFAULT_FALLBACK_MODEL", "mistral-31-24b"))
    
    # Legacy Model Sync Settings (deprecated - kept for compatibility)
    MODEL_SYNC_ON_STARTUP: bool = Field(default=False)  # Disabled by default
    MODEL_SYNC_INTERVAL_HOURS: int = Field(default=int(os.getenv("MODEL_SYNC_INTERVAL_HOURS", "1")))
    MODEL_SYNC_ENABLED: bool = Field(default=False)  # Disabled by default
    


    class Config:
        env_file = ".env"
        env_file_encoding = 'utf-8'
        case_sensitive = True
        
        # Allow extra fields from environment variables
        extra = "ignore"
        
        # Allow env variables to be parsed as complex types
        validate_assignment = True

settings = Settings() 