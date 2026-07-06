from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import psycopg
import redis
from elasticsearch import Elasticsearch
from neo4j import Driver, GraphDatabase
from psycopg.connection import Connection
from redis import Redis

from ingestion.config import IngestionSettings


@dataclass(frozen=True)
class PostgresClient:
    database_url: str

    @classmethod
    def from_settings(cls, settings: IngestionSettings) -> "PostgresClient":
        return cls(database_url=settings.database_url)

    @contextmanager
    def connect(self) -> Iterator[Connection[Any]]:
        with psycopg.connect(self.database_url) as connection:
            yield connection

    def ping(self) -> bool:
        with self.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                return cursor.fetchone() == (1,)


@dataclass(frozen=True)
class Neo4jClient:
    uri: str
    username: str
    password: str

    @classmethod
    def from_settings(cls, settings: IngestionSettings) -> "Neo4jClient":
        return cls(
            uri=settings.neo4j_uri,
            username=settings.neo4j_user,
            password=settings.neo4j_password,
        )

    def driver(self) -> Driver:
        return GraphDatabase.driver(self.uri, auth=(self.username, self.password))

    def ping(self) -> bool:
        with self.driver() as driver:
            driver.verify_connectivity()
        return True


@dataclass(frozen=True)
class ElasticsearchClient:
    url: str

    @classmethod
    def from_settings(cls, settings: IngestionSettings) -> "ElasticsearchClient":
        return cls(url=settings.elasticsearch_url)

    def client(self) -> Elasticsearch:
        return Elasticsearch(self.url)

    def ping(self) -> bool:
        return self.client().ping()


@dataclass(frozen=True)
class RedisClient:
    url: str

    @classmethod
    def from_settings(cls, settings: IngestionSettings) -> "RedisClient":
        return cls(url=settings.redis_url)

    def client(self) -> Redis:
        return redis.Redis.from_url(self.url)

    def ping(self) -> bool:
        return bool(self.client().ping())


@dataclass(frozen=True)
class DatastoreClients:
    postgres: PostgresClient
    neo4j: Neo4jClient
    elasticsearch: ElasticsearchClient
    redis: RedisClient

    @classmethod
    def from_settings(cls, settings: IngestionSettings) -> "DatastoreClients":
        return cls(
            postgres=PostgresClient.from_settings(settings),
            neo4j=Neo4jClient.from_settings(settings),
            elasticsearch=ElasticsearchClient.from_settings(settings),
            redis=RedisClient.from_settings(settings),
        )

    def healthcheck(self) -> dict[str, bool]:
        return {
            "postgres": self.postgres.ping(),
            "neo4j": self.neo4j.ping(),
            "elasticsearch": self.elasticsearch.ping(),
            "redis": self.redis.ping(),
        }
