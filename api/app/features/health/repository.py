from dataclasses import dataclass
from typing import Any, Literal, Protocol

import psycopg
from elasticsearch import Elasticsearch
from neo4j import GraphDatabase
from redis import Redis

from api.app.core.settings import Settings


StoreHealthStatus = Literal["ok", "error"]


@dataclass(frozen=True)
class StoreHealth:
    name: str
    status: StoreHealthStatus
    detail: str | None = None


class DatastoreHealthClient(Protocol):
    name: str

    def check(self) -> StoreHealth:
        """Return the datastore's current health."""


class PostgresHealthClient:
    name = "postgres"

    def __init__(self, settings: Settings) -> None:
        self._database_url = settings.database_url

    def check(self) -> StoreHealth:
        try:
            with psycopg.connect(self._database_url) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
            return StoreHealth(name=self.name, status="ok")
        except Exception as exc:
            return _error_health(self.name, exc)


class RedisHealthClient:
    name = "redis"

    def __init__(self, settings: Settings) -> None:
        self._redis_url = settings.redis_url

    def check(self) -> StoreHealth:
        try:
            client = Redis.from_url(self._redis_url)
            client.ping()
            return StoreHealth(name=self.name, status="ok")
        except Exception as exc:
            return _error_health(self.name, exc)


class Neo4jHealthClient:
    name = "neo4j"

    def __init__(self, settings: Settings) -> None:
        self._uri = settings.neo4j_uri
        self._user = settings.neo4j_user
        self._password = settings.neo4j_password

    def check(self) -> StoreHealth:
        driver: Any | None = None

        try:
            driver = GraphDatabase.driver(self._uri, auth=(self._user, self._password))
            driver.verify_connectivity()
            return StoreHealth(name=self.name, status="ok")
        except Exception as exc:
            return _error_health(self.name, exc)
        finally:
            if driver is not None:
                driver.close()


class ElasticsearchHealthClient:
    name = "elasticsearch"

    def __init__(self, settings: Settings) -> None:
        self._url = settings.elasticsearch_url

    def check(self) -> StoreHealth:
        client: Any | None = None

        try:
            client = Elasticsearch(self._url)
            if not client.ping():
                return StoreHealth(name=self.name, status="error", detail="Ping returned false")
            return StoreHealth(name=self.name, status="ok")
        except Exception as exc:
            return _error_health(self.name, exc)
        finally:
            if client is not None:
                client.close()


def build_datastore_health_clients(settings: Settings) -> tuple[DatastoreHealthClient, ...]:
    return (
        PostgresHealthClient(settings),
        RedisHealthClient(settings),
        Neo4jHealthClient(settings),
        ElasticsearchHealthClient(settings),
    )


def _error_health(name: str, exc: Exception) -> StoreHealth:
    return StoreHealth(name=name, status="error", detail=exc.__class__.__name__)
