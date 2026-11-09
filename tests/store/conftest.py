"""Postgres fixtures for store tests.

Skipped tests do not connect. When the URLs are set, the session applies
Alembic migrations with the admin role and each test starts from empty
data tables. audit_head goes back to event 0 and 64 zero digits, so a
chain written by one test is not visible to the next.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[2]


def database_configured() -> bool:
    """True when both the app URL and the admin URL are set."""
    return bool(os.environ.get("DATABASE_URL") and os.environ.get("DATABASE_ADMIN_URL"))


@pytest.fixture(scope="session", autouse=True)
def migrated() -> None:
    """Apply migrations once per session. No-op when the database is not configured."""
    if not database_configured():
        return
    from alembic.config import Config

    from alembic import command

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(cfg, "head")


@pytest.fixture
def admin() -> Iterator[psycopg.Connection]:
    """Admin connection. Data tables are empty and the chain head is reset."""
    if not database_configured():
        reason = (
            "DATABASE_URL is unset"
            if not os.environ.get("DATABASE_URL")
            else "DATABASE_ADMIN_URL is unset"
        )
        pytest.skip(reason)
    with psycopg.connect(
        os.environ["DATABASE_ADMIN_URL"], row_factory=dict_row, connect_timeout=5
    ) as conn:
        conn.execute(
            """
            TRUNCATE TABLE
                audit_event,
                finding,
                review_run,
                batch_step,
                batch,
                master_step,
                master_record
            """
        )
        conn.execute(
            """
            UPDATE audit_head
            SET last_event_seq = 0,
                last_event_hash = repeat('0', 64)
            WHERE id = 1
            """
        )
        conn.commit()
        yield conn
        conn.rollback()
