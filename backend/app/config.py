"""Application settings loaded from the root .env file."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve root .env regardless of working directory:
# backend/app/config.py → backend/app → backend → repo root
_ROOT_ENV = Path(__file__).resolve().parent.parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ROOT_ENV),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    DATABASE_URL: str
    REDIS_URL: str

    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_DAYS: int = 7

    GEMINI_API_KEY: str
    GEMINI_BASE_URL: str
    LLM_MODEL: str


settings = Settings()
