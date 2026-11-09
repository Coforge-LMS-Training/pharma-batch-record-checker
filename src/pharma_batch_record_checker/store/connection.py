"""Connections for the store.

The app role comes from DATABASE_URL. Migrations use DATABASE_ADMIN_URL and
do not go through this module.
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg import Connection


def connect() -> Connection:
    """Open a connection as the app role."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg.connect(url)


@contextmanager
def transaction() -> Iterator[Connection]:
    """One transaction. Any exception rolls the write back."""
    with connect() as conn, conn.transaction():
        yield conn
