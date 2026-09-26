"""A fresh PostgreSQL database per test module, dropped afterwards.

Integration tests that write vehicles must never touch the database
`DATABASE_URL` points at: locally that is a full copy of live. Each module gets
its own database on the same server, named so a leftover is recognizable.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import uuid4

import psycopg
import pytest
from psycopg import Connection, sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from ingestion.config import get_ingestion_settings


@contextmanager
def throwaway_database(prefix: str) -> Iterator[Connection]:
    settings = get_ingestion_settings()
    try:
        admin = psycopg.connect(settings.database_url, autocommit=True)
    except psycopg.OperationalError:
        if settings.environment == "test":
            raise
        pytest.skip("PostgreSQL is unavailable; start it with docker compose up -d postgres")
    name = f"nstest_{prefix}_{uuid4().hex[:10]}"
    try:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        params = conninfo_to_dict(settings.database_url)
        params["dbname"] = name
        connection = psycopg.connect(make_conninfo(**params))
        try:
            yield connection
        finally:
            connection.close()
    finally:
        admin.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
        )
        admin.close()
