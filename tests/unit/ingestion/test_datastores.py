from ingestion.config import IngestionSettings
from ingestion.datastores import (
    DatastoreClients,
    ElasticsearchClient,
    Neo4jClient,
    PostgresClient,
    RedisClient,
)


def test_datastore_clients_are_built_from_settings() -> None:
    settings = IngestionSettings(
        DATABASE_URL="postgresql://app:password@postgres:5432/app",
        NEO4J_URI="bolt://neo4j:7687",
        NEO4J_USER="neo4j",
        NEO4J_PASSWORD="password",
        ELASTICSEARCH_URL="http://elasticsearch:9200",
        REDIS_URL="redis://redis:6379/0",
    )

    clients = DatastoreClients.from_settings(settings)

    assert clients.postgres.database_url == "postgresql://app:password@postgres:5432/app"
    assert clients.neo4j.uri == "bolt://neo4j:7687"
    assert clients.neo4j.username == "neo4j"
    assert clients.neo4j.password == "password"
    assert clients.elasticsearch.url == "http://elasticsearch:9200"
    assert clients.redis.url == "redis://redis:6379/0"


def test_datastore_healthcheck_aggregates_client_ping_results(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    clients = DatastoreClients(
        postgres=PostgresClient(database_url="postgresql://example"),
        neo4j=Neo4jClient(uri="bolt://example:7687", username="neo4j", password="password"),
        elasticsearch=ElasticsearchClient(url="http://example:9200"),
        redis=RedisClient(url="redis://example:6379/0"),
    )

    monkeypatch.setattr(PostgresClient, "ping", lambda self: True)
    monkeypatch.setattr(Neo4jClient, "ping", lambda self: True)
    monkeypatch.setattr(ElasticsearchClient, "ping", lambda self: False)
    monkeypatch.setattr(RedisClient, "ping", lambda self: True)

    result = clients.healthcheck()

    assert result == {
        "postgres": True,
        "neo4j": True,
        "elasticsearch": False,
        "redis": True,
    }
