from pathlib import Path
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SONIC_", env_file=".env", extra="ignore")
    database_url: SecretStr
    jwt_secret: SecretStr
    storage_root: Path = Path("data/audio")
    allow_registration: bool = False
    token_minutes: int = Field(default=43200, ge=5, le=43200)
    max_upload_bytes: int = Field(default=25 * 1024 * 1024, ge=1024, le=100 * 1024 * 1024)
    auth_limit: int = Field(default=10, ge=1)

    @field_validator("database_url")
    @classmethod
    def postgres_only(cls, value):
        if not value.get_secret_value().startswith("postgresql+psycopg://"):
            raise ValueError("PostgreSQL with psycopg is required")
        return value

    @field_validator("jwt_secret")
    @classmethod
    def strong_secret(cls, value):
        if len(value.get_secret_value()) < 32:
            raise ValueError("JWT secret must have at least 32 characters")
        return value
