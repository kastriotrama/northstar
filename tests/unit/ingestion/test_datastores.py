from ingestion.config import IngestionSettings
from ingestion.datastores import DatastoreClients


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

