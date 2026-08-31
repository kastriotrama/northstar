from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Vehicle Intelligence API"
    app_version: str = "0.1.0"

    environment: str = Field(default="local", alias="ENVIRONMENT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")

    cors_origins_raw: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000",
        alias="CORS_ORIGINS",
    )

    database_url: str = Field(
        default="postgresql://app:change_me@localhost:5432/app",
        alias="DATABASE_URL",
    )

    neo4j_uri: str = Field(default="bolt://localhost:7687", alias="NEO4J_URI")
    neo4j_user: str = Field(default="neo4j", alias="NEO4J_USER")
    neo4j_password: str = Field(default="change_me", alias="NEO4J_PASSWORD")

    elasticsearch_url: str = Field(
        default="http://localhost:9200",
        alias="ELASTICSEARCH_URL",
    )

    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    health_check_timeout_seconds: int = Field(
        default=2,
        alias="HEALTH_CHECK_TIMEOUT_SECONDS",
        ge=1,
    )

    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    rule_advisor_model: str = Field(
        default="gpt-4o-mini", alias="RULE_ADVISOR_MODEL"
    )
    rule_advisor_base_url: str = Field(
        default="https://api.openai.com/v1", alias="RULE_ADVISOR_BASE_URL"
    )
    rule_advisor_timeout_seconds: float = Field(
        default=30.0, alias="RULE_ADVISOR_TIMEOUT_SECONDS", gt=0
    )

    oem_vin_provider_name: str | None = Field(
        default=None, alias="OEM_VIN_PROVIDER_NAME"
    )
    oem_vin_provider_base_url: str | None = Field(
        default=None, alias="OEM_VIN_PROVIDER_BASE_URL"
    )
    oem_vin_provider_api_key: str | None = Field(
        default=None, alias="OEM_VIN_PROVIDER_API_KEY"
    )
    oem_vin_provider_dataset_version: str = Field(
        default="unversioned", alias="OEM_VIN_PROVIDER_DATASET_VERSION"
    )
    oem_vin_provider_timeout_seconds: float = Field(
        default=15.0, alias="OEM_VIN_PROVIDER_TIMEOUT_SECONDS", gt=0
    )
    resolved_match_showcase_path: Path = Field(
        default=Path("outputs/ts-ktype-resolved-showcase-1000.json"),
        alias="RESOLVED_MATCH_SHOWCASE_PATH",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins_raw.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
