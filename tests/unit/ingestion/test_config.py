import pytest
from pydantic import ValidationError

from ingestion.config import IngestionSettings


def test_ingestion_settings_loads_environment_aliases() -> None:
    settings = IngestionSettings(
        ENVIRONMENT="test",
        LOG_LEVEL="DEBUG",
        DATABASE_URL="postgresql://example",
        NEO4J_URI="bolt://example:7687",
        NEO4J_USER="user",
        NEO4J_PASSWORD="password",
        ELASTICSEARCH_URL="http://example:9200",
        REDIS_URL="redis://example:6379/0",
        INGESTION_BATCH_SIZE=25,
        TECDOC_SOURCE_PATH="/tmp/tecdoc",
        TRANSPORTSTYRELSEN_SOURCE_PATH="/tmp/transportstyrelsen",
    )

    assert settings.environment == "test"
    assert settings.log_level == "DEBUG"
    assert settings.database_url == "postgresql://example"
    assert settings.neo4j_uri == "bolt://example:7687"
    assert settings.neo4j_user == "user"
    assert settings.neo4j_password == "password"
    assert settings.elasticsearch_url == "http://example:9200"
    assert settings.redis_url == "redis://example:6379/0"
    assert settings.ingestion_batch_size == 25
    assert settings.tecdoc_source_path == "/tmp/tecdoc"
    assert settings.transportstyrelsen_source_path == "/tmp/transportstyrelsen"
    assert settings.source_paths == {
        "tecdoc": "/tmp/tecdoc",
        "transportstyrelsen": "/tmp/transportstyrelsen",
    }


def test_log_level_is_normalized() -> None:
    settings = IngestionSettings(LOG_LEVEL="debug")

    assert settings.log_level == "DEBUG"


def test_invalid_log_level_is_rejected() -> None:
    with pytest.raises(ValidationError, match="LOG_LEVEL must be one of"):
        IngestionSettings(LOG_LEVEL="verbose")


def test_batch_size_must_be_positive() -> None:
    with pytest.raises(ValidationError, match="INGESTION_BATCH_SIZE must be greater than 0"):
        IngestionSettings(INGESTION_BATCH_SIZE=0)
