import psycopg
from elasticsearch import Elasticsearch
from neo4j import GraphDatabase
from redis import Redis

from api.app.core.health import DatastoreHealthClient, StoreHealth
from api.app.core.settings import Settings


class PostgresHealthClient:
    name = "postgres"

    def __init__(self, settings: Settings) -> None:
        self._database_url = settings.database_url
        self._timeout = settings.health_check_timeout_seconds

    def check(self) -> StoreHealth:
        with psycopg.connect(self._database_url, connect_timeout=self._timeout) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
        return StoreHealth(name=self.name, status="ok")


class RedisHealthClient:
    name = "redis"

    def __init__(self, settings: Settings) -> None:
        self._redis_url = settings.redis_url
        self._timeout = settings.health_check_timeout_seconds

    def check(self) -> StoreHealth:
        client = Redis.from_url(
            self._redis_url,
            socket_connect_timeout=self._timeout,
            socket_timeout=self._timeout,
        )
        try:
            client.ping()
        finally:
            client.close()
        return StoreHealth(name=self.name, status="ok")


class Neo4jHealthClient:
    name = "neo4j"

    def __init__(self, settings: Settings) -> None:
        self._uri = settings.neo4j_uri
        self._user = settings.neo4j_user
        self._password = settings.neo4j_password
        self._timeout = settings.health_check_timeout_seconds

    def check(self) -> StoreHealth:
        driver = GraphDatabase.driver(
            self._uri,
            auth=(self._user, self._password),
            connection_timeout=self._timeout,
        )
        try:
            driver.verify_connectivity()
        finally:
            driver.close()
        return StoreHealth(name=self.name, status="ok")


class ElasticsearchHealthClient:
    name = "elasticsearch"

    def __init__(self, settings: Settings) -> None:
        self._url = settings.elasticsearch_url
        self._timeout = settings.health_check_timeout_seconds

    def check(self) -> StoreHealth:
        client = Elasticsearch(self._url, request_timeout=self._timeout)
        try:
            if not client.ping():
                return StoreHealth(name=self.name, status="error", detail="Ping returned false")
        finally:
            client.close()
        return StoreHealth(name=self.name, status="ok")


def build_datastore_health_clients(settings: Settings) -> tuple[DatastoreHealthClient, ...]:
    return (
        PostgresHealthClient(settings),
        RedisHealthClient(settings),
        Neo4jHealthClient(settings),
        ElasticsearchHealthClient(settings),
    )
