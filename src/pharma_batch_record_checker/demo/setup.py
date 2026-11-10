"""Prepare the local database before the demo serves pages.

A fresh clone has no tables and no sample. This module fills in the local
URLs, starts the Compose Postgres when it is not already accepting
connections, applies the migrations, and loads the approved sample. A sample
that is already stored is left as it is.
"""

import os
import subprocess
import urllib.parse
from pathlib import Path

import psycopg

LOCAL_APP_URL = "postgresql://pharma_app:pharma_app@localhost:5432/pharma"
LOCAL_ADMIN_URL = "postgresql://pharma_admin:pharma_admin@localhost:5432/pharma"


def prepare() -> None:
    """Make the local database ready, then return."""
    os.environ.setdefault("DATABASE_URL", LOCAL_APP_URL)
    os.environ.setdefault("DATABASE_ADMIN_URL", LOCAL_ADMIN_URL)
    root = repo_root()
    admin_url = os.environ["DATABASE_ADMIN_URL"]
    if is_local(admin_url) and not postgres_accepts(admin_url):
        compose_up(root)
    print("Applying migrations...")
    migrate(root)
    if load_sample(root):
        print("Approved sample is already loaded.")
    else:
        print("Loaded the approved sample.")


def repo_root() -> Path:
    """Directory that contains docker-compose.yml and the approved sample."""
    starts = (Path.cwd(), Path(__file__).resolve().parents[3])
    for start in starts:
        found = find_root(start)
        if found is not None:
            return found
    raise SystemExit(
        "Run pharma-review-demo from the repository. docker-compose.yml was not found."
    )


def find_root(start: Path) -> Path | None:
    """Walk parents of start for the Compose file and the approved sample."""
    for path in (start, *start.parents):
        compose = path / "docker-compose.yml"
        sample = path / "fixtures" / "approved_sample" / "master_record.csv"
        if compose.is_file() and sample.is_file():
            return path
    return None


def is_local(url: str) -> bool:
    """True for the Compose Postgres on localhost port 5432."""
    parsed = urllib.parse.urlparse(url)
    return (
        parsed.hostname in {"localhost", "127.0.0.1"} and (parsed.port or 5432) == 5432
    )


def postgres_accepts(url: str) -> bool:
    """True when that URL accepts a connection."""
    try:
        with psycopg.connect(url, connect_timeout=2) as conn:
            conn.execute("SELECT 1")
    except psycopg.OperationalError:
        return False
    return True


def compose_up(root: Path) -> None:
    """Start the Compose Postgres and wait until it is healthy."""
    print("Starting local Postgres...")
    try:
        subprocess.run(
            ["docker", "compose", "up", "-d", "--wait"],
            cwd=root,
            check=True,
        )
    except FileNotFoundError as exc:
        raise SystemExit(
            "Docker is required for the local database. "
            "Install Docker, then run this command again."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise SystemExit(
            f"Docker did not start Postgres (exit {exc.returncode})."
        ) from exc


def migrate(root: Path) -> None:
    """Apply Alembic migrations with DATABASE_ADMIN_URL."""
    from alembic.config import Config

    from alembic import command

    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(cfg, "head")


def load_sample(root: Path) -> bool:
    """Load the approved sample. Return True when it was already stored."""
    from pharma_batch_record_checker.store.errors import DuplicateRecord
    from pharma_batch_record_checker.store.load import load_and_review
    from pharma_batch_record_checker.store.review_store import PostgresReviewStore

    sample = root / "fixtures" / "approved_sample"
    try:
        load_and_review(
            sample / "master_record.csv",
            sample / "batch_record.csv",
            PostgresReviewStore(),
        )
    except DuplicateRecord:
        return True
    return False
