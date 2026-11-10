"""Prepare the local database before the demo serves pages.

A fresh clone has no tables and no sample. This module fills in the local
URLs, starts the Compose Postgres when it is not already accepting
connections, applies the migrations, and loads the approved sample, or a
generated data folder instead when one is given. Rows that are already stored
are left as they are.
"""

import os
import subprocess
import urllib.parse
from pathlib import Path

import psycopg

LOCAL_APP_URL = "postgresql://pharma_app:pharma_app@localhost:5432/pharma"
LOCAL_ADMIN_URL = "postgresql://pharma_admin:pharma_admin@localhost:5432/pharma"

# The two files a dataset folder must hold, master first. The app does not
# import the generator, so the names are written here, as in the CSV contract.
DATASET_FILES = ("master_record.csv", "batch_record.csv")
# The approved sample's master file, relative to the root (marks a usable root).
SAMPLE_MASTER = Path("fixtures") / "approved_sample" / "master_record.csv"


def prepare(data: Path | None = None) -> None:
    """Make the local database ready, then return.

    With data, load that folder (e.g. one written by generate-batch-records)
    instead of the approved sample. The folder is checked before Postgres
    starts, so a wrong path fails fast.
    """
    if data is not None:
        check_dataset(data)
    os.environ.setdefault("DATABASE_URL", LOCAL_APP_URL)
    os.environ.setdefault("DATABASE_ADMIN_URL", LOCAL_ADMIN_URL)
    root = repo_root()
    admin_url = os.environ["DATABASE_ADMIN_URL"]
    if is_local(admin_url) and not postgres_accepts(admin_url):
        compose_up(root)
    print("Applying migrations...")
    migrate(root)
    if data is not None:
        if load_dataset(data):
            print(
                f"Nothing new loaded from {data}: its masters or batches are "
                "already stored. The generated masters match the approved "
                "sample's, so start from an empty local database to view only "
                "this dataset: docker compose down -v, then run this command again."
            )
        else:
            print(f"Loaded {data}.")
    elif load_sample(root):
        print("Approved sample is already loaded.")
    else:
        print("Loaded the approved sample.")


def repo_root() -> Path:
    """Directory that contains the migrations and the approved sample.

    PHARMA_ROOT, when set, names it directly (the container image has no
    docker-compose.yml); it must hold alembic.ini and the approved sample.
    Otherwise the repository is found by walking up from the working
    directory, then from this package, to docker-compose.yml.
    """
    if env_root := os.environ.get("PHARMA_ROOT"):
        root = Path(env_root)
        needed = (root / "alembic.ini", root / SAMPLE_MASTER)
        if missing := [str(path) for path in needed if not path.is_file()]:
            raise SystemExit(f"PHARMA_ROOT={root} has no {' or '.join(missing)}.")
        return root
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
        if compose.is_file() and (path / SAMPLE_MASTER).is_file():
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
    return load_dataset(root / "fixtures" / "approved_sample")


def check_dataset(folder: Path) -> None:
    """Stop with one message if folder lacks the master or batch CSV."""
    missing = [name for name in DATASET_FILES if not (folder / name).is_file()]
    if missing:
        raise SystemExit(
            f"{folder} has no {' or '.join(missing)}. "
            f"Run: uv run generate-batch-records --out {folder} "
            "(in Docker: docker compose run --rm generator)"
        )


def load_dataset(folder: Path) -> bool:
    """Review and store folder's master and batch CSVs.

    Return True when DuplicateRecord says something in them is already stored.
    """
    from pharma_batch_record_checker.store.errors import DuplicateRecord
    from pharma_batch_record_checker.store.load import load_and_review
    from pharma_batch_record_checker.store.review_store import PostgresReviewStore

    master, batch = (folder / name for name in DATASET_FILES)
    try:
        load_and_review(master, batch, PostgresReviewStore())
    except DuplicateRecord:
        return True
    return False
