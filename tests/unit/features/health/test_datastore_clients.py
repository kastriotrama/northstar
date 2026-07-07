from unittest.mock import MagicMock, patch

from api.app.core.settings import Settings
from api.app.features.health.repository import (
    ElasticsearchHealthClient,
    Neo4jHealthClient,
    PostgresHealthClient,
    RedisHealthClient,
    StoreHealth,
    build_datastore_health_clients,
)


def make_settings() -> Settings:
    return Settings(
        DATABASE_URL="postgresql://test:test@postgres:5432/test",
        REDIS_URL="redis://redis:6379/1",
        NEO4J_URI="bolt://neo4j:7687",
        NEO4J_USER="neo4j-test",
        NEO4J_PASSWORD="secret",
        ELASTICSEARCH_URL="http://elasticsearch:9200",
    )


def test_build_datastore_health_clients_does_not_connect_at_construction() -> None:
    settings = make_settings()

    with (
        patch("api.app.features.health.repository.psycopg.connect") as postgres_connect,
        patch("api.app.features.health.repository.Redis.from_url") as redis_from_url,
        patch("api.app.features.health.repository.GraphDatabase.driver") as neo4j_driver,
        patch("api.app.features.health.repository.Elasticsearch") as elasticsearch_client,
    ):
        clients = build_datastore_health_clients(settings)

    assert [client.name for client in clients] == [
        "postgres",
        "redis",
        "neo4j",
        "elasticsearch",
    ]
    postgres_connect.assert_not_called()
    redis_from_url.assert_not_called()
    neo4j_driver.assert_not_called()
    elasticsearch_client.assert_not_called()


def test_postgres_health_client_uses_injected_database_url() -> None:
    settings = make_settings()
    connection_context = MagicMock()
    connection = MagicMock()
    cursor_context = MagicMock()
    cursor = MagicMock()
    connection_context.__enter__.return_value = connection
    connection.cursor.return_value = cursor_context
    cursor_context.__enter__.return_value = cursor

    with patch(
        "api.app.features.health.repository.psycopg.connect",
        return_value=connection_context,
    ) as connect:
        result = PostgresHealthClient(settings).check()

    assert result == StoreHealth(name="postgres", status="ok")
    connect.assert_called_once_with(settings.database_url)
    cursor.execute.assert_called_once_with("SELECT 1")


def test_postgres_health_client_returns_error_status_on_failure() -> None:
    settings = make_settings()

    with patch(
        "api.app.features.health.repository.psycopg.connect",
        side_effect=RuntimeError("database unavailable"),
    ):
        result = PostgresHealthClient(settings).check()

    assert result == StoreHealth(name="postgres", status="error", detail="RuntimeError")


def test_redis_health_client_uses_injected_redis_url() -> None:
    settings = make_settings()
    redis_client = MagicMock()

    with patch(
        "api.app.features.health.repository.Redis.from_url",
        return_value=redis_client,
    ) as from_url:
        result = RedisHealthClient(settings).check()

    assert result == StoreHealth(name="redis", status="ok")
    from_url.assert_called_once_with(settings.redis_url)
    redis_client.ping.assert_called_once_with()


def test_neo4j_health_client_uses_injected_connection_settings() -> None:
    settings = make_settings()
    driver = MagicMock()

    with patch(
        "api.app.features.health.repository.GraphDatabase.driver",
        return_value=driver,
    ) as driver_factory:
        result = Neo4jHealthClient(settings).check()

    assert result == StoreHealth(name="neo4j", status="ok")
    driver_factory.assert_called_once_with(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
    )
    driver.verify_connectivity.assert_called_once_with()
    driver.close.assert_called_once_with()


def test_elasticsearch_health_client_uses_injected_url() -> None:
    settings = make_settings()
    client = MagicMock()
    client.ping.return_value = True

    with patch(
        "api.app.features.health.repository.Elasticsearch",
        return_value=client,
    ) as client_factory:
        result = ElasticsearchHealthClient(settings).check()

    assert result == StoreHealth(name="elasticsearch", status="ok")
    client_factory.assert_called_once_with(settings.elasticsearch_url)
    client.ping.assert_called_once_with()
    client.close.assert_called_once_with()


def test_elasticsearch_health_client_returns_error_when_ping_fails() -> None:
    settings = make_settings()
    client = MagicMock()
    client.ping.return_value = False

    with patch("api.app.features.health.repository.Elasticsearch", return_value=client):
        result = ElasticsearchHealthClient(settings).check()

    assert result == StoreHealth(
        name="elasticsearch",
        status="error",
        detail="Ping returned false",
    )
    client.close.assert_called_once_with()
