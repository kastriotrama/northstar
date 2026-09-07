from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.connection import Connection

from api.app.core.settings import Settings, get_settings


@contextmanager
def get_postgres_connection(settings: Settings | None = None) -> Iterator[Connection[Any]]:
    resolved_settings = settings or get_settings()

    with psycopg.connect(resolved_settings.database_url) as connection:
        yield connection
