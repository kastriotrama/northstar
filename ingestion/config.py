from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SUPPORTED_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


class IngestionSettings(BaseSettings):
    environment: str = Field(default="local", alias="ENVIRONMENT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    database_url: str = Field(
        default="postgresql://app:change_me@localhost:5432/app",
        alias="DATABASE_URL",
    )
    neo4j_uri: str = Field(default="bolt://localhost:7687", alias="NEO4J_URI")
    neo4j_user: str = Field(default="neo4j", alias="NEO4J_USER")
    neo4j_password: str = Field(default="change_me", alias="NEO4J_PASSWORD")
    elasticsearch_url: str = Field(default="http://localhost:9200", alias="ELASTICSEARCH_URL")
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    ingestion_batch_size: int = Field(default=500, alias="INGESTION_BATCH_SIZE")
    tecdoc_source_path: str | None = Field(default=None, alias="TECDOC_SOURCE_PATH")
    transportstyrelsen_source_path: str | None = Field(
        default=None,
        alias="TRANSPORTSTYRELSEN_SOURCE_PATH",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        normalized_value = value.upper()
        if normalized_value not in SUPPORTED_LOG_LEVELS:
            supported_values = ", ".join(sorted(SUPPORTED_LOG_LEVELS))
            message = f"LOG_LEVEL must be one of: {supported_values}"
            raise ValueError(message)

        return normalized_value

    @field_validator("ingestion_batch_size")
    @classmethod
    def validate_batch_size(cls, value: int) -> int:
        if value < 1:
            message = "INGESTION_BATCH_SIZE must be greater than 0"
            raise ValueError(message)

        return value

    @property
    def source_paths(self) -> dict[str, str | None]:
        return {
            "tecdoc": self.tecdoc_source_path,
            "transportstyrelsen": self.transportstyrelsen_source_path,
        }


@lru_cache
def get_ingestion_settings() -> IngestionSettings:
    return IngestionSettings()
