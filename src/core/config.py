import os
from typing import List, Union, Optional, Any
from pydantic_settings import BaseSettings
from pydantic import PostgresDsn, RedisDsn, Field, AnyHttpUrl, field_validator
from dotenv import load_dotenv

# Load .env file variables
load_dotenv()

class Settings(BaseSettings):
    # Project Settings
    PROJECT_NAME: str = "Morpheus API Gateway"
    API_V1_STR: str = "/api/v1"
    
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
    
    # Database Connection
    DATABASE_URL: PostgresDsn = Field(default=os.getenv("DATABASE_URL", "postgresql+asyncpg://morpheus_user:morpheus_password@localhost:5432/morpheus_db"))
    
    # Database Settings (placeholders for Docker)
    DB_USER: str = Field(default=os.getenv("POSTGRES_USER", "morpheus_user"))
    DB_PASSWORD: str = Field(default=os.getenv("POSTGRES_PASSWORD", "secure_password_here"))
    DB_NAME: str = Field(default=os.getenv("POSTGRES_DB", "morpheus_db"))

    # Redis Settings
    REDIS_URL: RedisDsn = Field(default=os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    REDIS_PASSWORD: str = Field(default=os.getenv("REDIS_PASSWORD", "secure_redis_password_here"))

    # JWT Settings
    JWT_SECRET_KEY: str = Field(default=os.getenv("JWT_SECRET_KEY", "super_secret_key_change_me"))
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
    REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))

    # Proxy Router Settings
    PROXY_ROUTER_URL: str = Field(default=os.getenv("PROXY_ROUTER_URL", ""))
    PROXY_ROUTER_USERNAME: str = Field(default=os.getenv("PROXY_ROUTER_USERNAME", ""))
    PROXY_ROUTER_PASSWORD: str = Field(default=os.getenv("PROXY_ROUTER_PASSWORD", ""))

    # KMS Settings (placeholders - specific config depends on KMS choice)
    KMS_PROVIDER: str | None = Field(default=os.getenv("KMS_PROVIDER", "aws"))
    KMS_MASTER_KEY_ID: str | None = Field(default=os.getenv("KMS_MASTER_KEY_ID"))
    
    # AWS KMS specific settings
    AWS_REGION: str | None = Field(default=os.getenv("AWS_REGION", "us-east-1"))
    AWS_ACCESS_KEY_ID: str | None = Field(default=os.getenv("AWS_ACCESS_KEY_ID"))
    AWS_SECRET_ACCESS_KEY: str | None = Field(default=os.getenv("AWS_SECRET_ACCESS_KEY"))
    AWS_SESSION_TOKEN: str | None = Field(default=os.getenv("AWS_SESSION_TOKEN"))
    
    # Local encryption key (for development)
    MASTER_ENCRYPTION_KEY: str | None = Field(default=os.getenv("MASTER_ENCRYPTION_KEY"))
    
    class Config:
        env_file = ".env"
        env_file_encoding = 'utf-8'
        case_sensitive = True
        
        # Allow extra fields from environment variables
        extra = "ignore"
        
        # Allow env variables to be parsed as complex types
        validate_assignment = True

settings = Settings() 