import os
from pydantic_settings import BaseSettings
from pydantic import PostgresDsn, RedisDsn, Field
from dotenv import load_dotenv

# Load .env file variables
load_dotenv()

class Settings(BaseSettings):
    # Project Settings
    PROJECT_NAME: str = "Morpheus API Gateway"
    API_V1_STR: str = "/api/v1"

    # Database Settings (using asyncpg driver)
    # Example: postgresql+asyncpg://user:password@host:port/db
    DATABASE_URL: PostgresDsn = Field(default=os.getenv("DATABASE_URL", "postgresql+asyncpg://morpheus_user:morpheus_password@localhost:5432/morpheus_db"))

    # Redis Settings
    # Example: redis://user:password@host:port/0
    REDIS_URL: RedisDsn = Field(default=os.getenv("REDIS_URL", "redis://localhost:6379/0"))

    # JWT Settings
    JWT_SECRET_KEY: str = Field(default=os.getenv("JWT_SECRET_KEY", "super_secret_key_change_me"))
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Proxy Router Settings
    PROXY_ROUTER_URL: str = Field(default=os.getenv("PROXY_ROUTER_URL", "http://localhost:8545")) # Example URL

    # KMS Settings (placeholders - specific config depends on KMS choice)
    KMS_PROVIDER: str | None = Field(default=os.getenv("KMS_PROVIDER", "aws")) # e.g., 'aws', 'gcp', 'azure', 'vault'
    KMS_MASTER_KEY_ID: str | None = Field(default=os.getenv("KMS_MASTER_KEY_ID"))
    
    # AWS KMS specific settings
    AWS_REGION: str | None = Field(default=os.getenv("AWS_REGION", "us-east-1"))
    AWS_ACCESS_KEY_ID: str | None = Field(default=os.getenv("AWS_ACCESS_KEY_ID"))
    AWS_SECRET_ACCESS_KEY: str | None = Field(default=os.getenv("AWS_SECRET_ACCESS_KEY"))
    AWS_SESSION_TOKEN: str | None = Field(default=os.getenv("AWS_SESSION_TOKEN"))
    # If running in AWS and using IAM roles, credentials can be omitted
    # Add other KMS specific settings as needed

    class Config:
        env_file = ".env"
        env_file_encoding = 'utf-8'
        case_sensitive = True

settings = Settings() 