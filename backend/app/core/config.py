"""Application Configuration using Pydantic Settings."""

from decimal import Decimal
from typing import Literal, Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central application settings validated at runtime."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # App Basics
    APP_NAME: str = "whatsapp-ai-munshi"
    APP_ENV: Literal["development", "staging", "production", "test"] = "development"
    DEBUG: bool = False
    PORT: int = 8000
    HOST: str = "0.0.0.0"
    API_V1_PREFIX: str = "/api/v1"
    SECRET_KEY: str = "munshi-insecure-dev-secret-key-change-in-production-min-32-chars"

    # Database
    DATABASE_URL: str = Field(
        default="sqlite+aiosqlite:///./munshi.db",
        description="Async database connection URL (PostgreSQL asyncpg or SQLite aiosqlite)",
    )
    DATABASE_SYNC_URL: str = Field(
        default="sqlite:///./munshi.db",
        description="Sync connection URL for Alembic migrations and synchronous tooling",
    )
    DB_ECHO: bool = False
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Celery (derived from REDIS_URL by default; can be overridden independently)
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # Groq LLM API
    GROQ_API_KEY: Optional[str] = None
    GROQ_MODEL: str = "llama-3.3-70b-versatile"
    LLM_PROVIDER: str = "groq"
    LLM_TEMPERATURE: float = 0.1
    LLM_MAX_TOKENS: int = 1024

    # Meta WhatsApp Cloud API
    WHATSAPP_ACCESS_TOKEN: Optional[str] = None
    WHATSAPP_PHONE_NUMBER_ID: Optional[str] = None
    WHATSAPP_VERIFY_TOKEN: str = "munshi_verify_token_change_in_production"
    WHATSAPP_APP_SECRET: Optional[str] = None
    WHATSAPP_API_VERSION: str = "v21.0"

    # RAG & ChromaDB
    CHROMA_HOST: str = "localhost"
    CHROMA_PORT: int = 8001
    CHROMA_COLLECTION_PREFIX: str = "munshi_shop_"
    EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"

    # JWT / Auth
    JWT_SECRET: str = "munshi-jwt-secret-key-must-be-at-least-32-bytes-long"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    # Business Harness Thresholds
    LARGE_CREDIT_APPROVAL_THRESHOLD: Decimal = Decimal("10000.00")
    LARGE_DISCOUNT_APPROVAL_THRESHOLD: Decimal = Decimal("2000.00")
    DEFAULT_SHOP_CURRENCY: str = "PKR"
    DEFAULT_SHOP_LANGUAGE: str = "roman_urdu"

    # Speech-to-Text (STT)
    STT_PROVIDER: str = "groq"                        # "groq" | "openai" | "mock"
    STT_GROQ_MODEL: str = "whisper-large-v3"          # Groq Whisper model
    STT_CONFIDENCE_THRESHOLD: float = 0.6             # below this → ask user to resend
    STT_MAX_AUDIO_SIZE_MB: int = 25                   # Meta limit is 16 MB; we allow up to 25


# Singleton instance
settings = Settings()
