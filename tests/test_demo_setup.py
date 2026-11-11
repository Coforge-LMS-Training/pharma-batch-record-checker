"""Startup for the review demo. These tests do not start Postgres."""

import os
from pathlib import Path

import pytest

from pharma_batch_record_checker.demo import cli, setup
from pharma_batch_record_checker.store.errors import DuplicateRecord


def test_prepare_fills_local_urls_and_skips_compose_when_postgres_is_up(monkeypatch):
    """A reachable local database is migrated and loaded without Docker."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_ADMIN_URL", raising=False)
    monkeypatch.setattr(setup, "repo_root", lambda: Path("/repo"))
    monkeypatch.setattr(setup, "postgres_accepts", lambda url: True)
    monkeypatch.setattr(setup, "migrate", lambda root: None)
    monkeypatch.setattr(setup, "load_sample", lambda root: False)

    def fail(root):
        raise AssertionError("compose should not start")

    monkeypatch.setattr(setup, "compose_up", fail)
    setup.prepare()
    assert os.environ["DATABASE_URL"] == setup.LOCAL_APP_URL
    assert os.environ["DATABASE_ADMIN_URL"] == setup.LOCAL_ADMIN_URL


def test_prepare_starts_compose_when_local_postgres_is_down(monkeypatch):
    """The local URL is started with Compose only when nothing is listening."""
    monkeypatch.setenv("DATABASE_URL", setup.LOCAL_APP_URL)
    monkeypatch.setenv("DATABASE_ADMIN_URL", setup.LOCAL_ADMIN_URL)
    started = []
    monkeypatch.setattr(setup, "repo_root", lambda: Path("/repo"))
    monkeypatch.setattr(setup, "postgres_accepts", lambda url: False)
    monkeypatch.setattr(setup, "compose_up", lambda root: started.append(root))
    monkeypatch.setattr(setup, "migrate", lambda root: None)
    monkeypatch.setattr(setup, "load_sample", lambda root: True)
    setup.prepare()
    assert started == [Path("/repo")]


def test_prepare_does_not_start_compose_for_a_remote_database(monkeypatch):
    """A database URL you set yourself is not replaced by the Compose service."""
    monkeypatch.setenv(
        "DATABASE_ADMIN_URL",
        "postgresql://pharma_admin:pharma_admin@db.example:5432/pharma",
    )
    monkeypatch.setattr(setup, "repo_root", lambda: Path("/repo"))
    monkeypatch.setattr(setup, "postgres_accepts", lambda url: False)
    monkeypatch.setattr(setup, "migrate", lambda root: None)
    monkeypatch.setattr(setup, "load_sample", lambda root: False)

    def fail(root):
        raise AssertionError("compose should not start")

    monkeypatch.setattr(setup, "compose_up", fail)
    setup.prepare()


def test_load_sample_treats_an_existing_master_as_already_loaded(monkeypatch, tmp_path):
    """DuplicateRecord means the sample is stored. The command continues."""

    def already_there(*args):
        raise DuplicateRecord("master MBR-SYN500 v3 already exists")

    monkeypatch.setattr(
        "pharma_batch_record_checker.store.load.load_and_review", already_there
    )
    assert setup.load_sample(tmp_path) is True


def test_load_sample_reports_a_new_load(monkeypatch, tmp_path):
    """A first load returns False so the command can say it stored the sample."""
    monkeypatch.setattr(
        "pharma_batch_record_checker.store.load.load_and_review", lambda *args: None
    )
    assert setup.load_sample(tmp_path) is False


def test_find_root_stops_at_the_compose_file(tmp_path):
    """The root is the directory with Compose and the approved master CSV."""
    root = tmp_path / "repo"
    sample = root / "fixtures" / "approved_sample"
    sample.mkdir(parents=True)
    (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (sample / "master_record.csv").write_text("id\n", encoding="utf-8")
    nested = root / "src" / "pkg"
    nested.mkdir(parents=True)
    assert setup.find_root(nested) == root


def test_no_setup_serves_without_preparing(monkeypatch):
    """--no-setup does not start Postgres or load the sample."""
    monkeypatch.setattr("sys.argv", ["pharma-review-demo", "--no-setup"])
    monkeypatch.setattr(
        cli,
        "prepare",
        lambda: (_ for _ in ()).throw(AssertionError("prepare")),
    )
    app = type("App", (), {"run": lambda self, **kwargs: kwargs})()
    monkeypatch.setattr(cli, "create_app", lambda: app)
    cli.main()


def test_main_prepares_before_serving(monkeypatch):
    """The default command prepares the database, then binds the pages."""
    monkeypatch.setattr("sys.argv", ["pharma-review-demo"])
    order = []
    monkeypatch.setattr(cli, "prepare", lambda: order.append("prepare"))
    app = type(
        "App",
        (),
        {"run": lambda self, **kwargs: order.append(("run", kwargs))},
    )()
    monkeypatch.setattr(cli, "create_app", lambda: app)
    cli.main()
    assert order[0] == "prepare"
    assert order[1][0] == "run"
    assert order[1][1]["host"] == "127.0.0.1"
    assert order[1][1]["port"] == 8000


def test_is_local_matches_only_the_compose_port():
    """Another host or port is left alone."""
    assert setup.is_local(setup.LOCAL_ADMIN_URL)
    assert not setup.is_local("postgresql://pharma_admin:secret@db.example:5432/pharma")
    assert not setup.is_local("postgresql://pharma_admin:secret@localhost:5433/pharma")


def test_compose_up_names_docker_when_it_is_missing(monkeypatch, tmp_path):
    """A machine without Docker gets one clear message."""

    def missing(*args, **kwargs):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(setup.subprocess, "run", missing)
    with pytest.raises(SystemExit, match="Docker is required"):
        setup.compose_up(tmp_path)


# --- Loading a generated data folder (--data) ---------------------------------


def write_dataset_files(folder):
    """Create placeholder master_record.csv and batch_record.csv in folder."""
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("master_record.csv", "batch_record.csv"):
        (folder / name).write_text("x\n", encoding="utf-8")
    return folder


def patch_database_steps(monkeypatch, calls):
    """Replace every database step of prepare() with a recorder in calls."""
    monkeypatch.setenv("DATABASE_URL", setup.LOCAL_APP_URL)
    monkeypatch.setenv("DATABASE_ADMIN_URL", setup.LOCAL_ADMIN_URL)
    monkeypatch.setattr(setup, "repo_root", lambda: Path("/repo"))
    monkeypatch.setattr(setup, "postgres_accepts", lambda url: False)
    monkeypatch.setattr(setup, "compose_up", lambda root: calls.append("compose"))
    monkeypatch.setattr(setup, "migrate", lambda root: calls.append("migrate"))
    monkeypatch.setattr(
        setup,
        "load_sample",
        lambda root: (_ for _ in ()).throw(AssertionError("sample must not load")),
    )


def test_load_dataset_passes_the_folder_csvs(monkeypatch, tmp_path):
    """load_dataset reviews and stores the folder's master and batch CSVs."""
    seen = []
    monkeypatch.setattr(
        "pharma_batch_record_checker.store.load.load_and_review",
        lambda master, batch, store: seen.append((master, batch)),
    )
    assert setup.load_dataset(tmp_path) is False
    assert seen == [(tmp_path / "master_record.csv", tmp_path / "batch_record.csv")]


def test_load_dataset_treats_duplicate_as_already_stored(monkeypatch, tmp_path):
    """DuplicateRecord means something in the folder is already stored."""

    def already_there(*args):
        raise DuplicateRecord("batch B26-0001 already exists")

    monkeypatch.setattr(
        "pharma_batch_record_checker.store.load.load_and_review", already_there
    )
    assert setup.load_dataset(tmp_path) is True


def test_load_sample_loads_the_approved_folder(monkeypatch, tmp_path):
    """load_sample is load_dataset on fixtures/approved_sample."""
    seen = []
    monkeypatch.setattr(setup, "load_dataset", lambda folder: seen.append(folder))
    setup.load_sample(tmp_path)
    assert seen == [tmp_path / "fixtures" / "approved_sample"]


def test_check_dataset_names_missing_files_and_the_generator_command(tmp_path):
    """A folder without the CSVs gets one message with the command that makes them."""
    with pytest.raises(SystemExit) as excinfo:
        setup.check_dataset(tmp_path)
    message = str(excinfo.value)
    assert "master_record.csv" in message
    assert "batch_record.csv" in message
    assert f"uv run generate-batch-records --out {tmp_path}" in message
    assert "docker compose run --rm generator" in message  # the Docker route
    setup.check_dataset(write_dataset_files(tmp_path))


def test_prepare_loads_the_data_folder_instead_of_the_sample(
    monkeypatch, tmp_path, capsys
):
    """With data, prepare migrates, then loads that folder and not the sample."""
    calls = []
    patch_database_steps(monkeypatch, calls)
    monkeypatch.setattr(
        setup, "load_dataset", lambda folder: calls.append(("load", folder)) or False
    )
    data = write_dataset_files(tmp_path / "data")
    setup.prepare(data)
    assert calls == ["compose", "migrate", ("load", data)]
    assert f"Loaded {data}." in capsys.readouterr().out


def test_prepare_checks_data_before_starting_postgres(monkeypatch, tmp_path):
    """A missing data folder stops before Docker or migrations run."""
    calls = []
    patch_database_steps(monkeypatch, calls)
    with pytest.raises(SystemExit, match="generate-batch-records"):
        setup.prepare(tmp_path / "missing")
    assert calls == []


def test_prepare_explains_an_overlapping_dataset(monkeypatch, tmp_path, capsys):
    """If the folder overlaps stored data, prepare says how to start empty."""
    patch_database_steps(monkeypatch, [])
    monkeypatch.setattr(setup, "load_dataset", lambda folder: True)
    data = write_dataset_files(tmp_path / "data")
    setup.prepare(data)
    out = capsys.readouterr().out
    assert f"Nothing new loaded from {data}" in out
    assert "docker compose down -v" in out


def fake_app(order):
    """An app whose run() records its call in order instead of serving."""
    return type("App", (), {"run": lambda self, **kwargs: order.append("run")})()


def test_main_passes_data_to_prepare(monkeypatch):
    """--data DIR prepares that folder, then serves."""
    monkeypatch.setattr("sys.argv", ["pharma-review-demo", "--data", "data"])
    order = []
    monkeypatch.setattr(cli, "prepare", lambda *args: order.append(("prepare", args)))
    monkeypatch.setattr(cli, "create_app", lambda: fake_app(order))
    cli.main()
    assert order == [("prepare", (Path("data"),)), "run"]


def test_main_without_data_prepares_the_sample(monkeypatch):
    """Without --data, prepare gets no arguments and loads the approved sample."""
    monkeypatch.setattr("sys.argv", ["pharma-review-demo"])
    order = []
    monkeypatch.setattr(cli, "prepare", lambda *args: order.append(("prepare", args)))
    monkeypatch.setattr(cli, "create_app", lambda: fake_app(order))
    cli.main()
    assert order == [("prepare", ()), "run"]


def test_data_with_no_setup_is_a_usage_error(monkeypatch, capsys):
    """--data needs setup to load anything, so --no-setup with it is refused."""
    monkeypatch.setattr(
        "sys.argv", ["pharma-review-demo", "--data", "data", "--no-setup"]
    )
    monkeypatch.setattr(
        cli, "create_app", lambda: (_ for _ in ()).throw(AssertionError("served"))
    )
    with pytest.raises(SystemExit) as excinfo:
        cli.main()
    assert excinfo.value.code == 2
    assert "--data" in capsys.readouterr().err


# --- PHARMA_ROOT (the container's root) ---------------------------------------


def make_root(path, *, alembic=True, sample=True):
    """A folder with alembic.ini and/or the approved master CSV, as the app needs."""
    path.mkdir(parents=True, exist_ok=True)
    if alembic:
        (path / "alembic.ini").write_text("[alembic]\n", encoding="utf-8")
    if sample:
        folder = path / "fixtures" / "approved_sample"
        folder.mkdir(parents=True)
        (folder / "master_record.csv").write_text("id\n", encoding="utf-8")
    return path


def test_pharma_root_is_used_when_set(monkeypatch, tmp_path):
    """In an image there is no docker-compose.yml: PHARMA_ROOT names the root."""
    root = make_root(tmp_path / "app")
    monkeypatch.setenv("PHARMA_ROOT", str(root))
    monkeypatch.chdir(tmp_path)
    assert setup.repo_root() == root


@pytest.mark.parametrize(
    ("kwargs", "missing"),
    [({"alembic": False}, "alembic.ini"), ({"sample": False}, "master_record.csv")],
    ids=["no_alembic_ini", "no_approved_sample"],
)
def test_pharma_root_missing_a_file_is_refused(monkeypatch, tmp_path, kwargs, missing):
    """A PHARMA_ROOT without the migrations or the sample stops with one message."""
    root = make_root(tmp_path / "app", **kwargs)
    monkeypatch.setenv("PHARMA_ROOT", str(root))
    with pytest.raises(SystemExit, match=missing):
        setup.repo_root()


def test_without_pharma_root_the_repository_is_found_as_before(monkeypatch, tmp_path):
    """Unset PHARMA_ROOT: the walk-up search for docker-compose.yml is unchanged."""
    root = make_root(tmp_path / "repo")
    (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    nested = root / "src"
    nested.mkdir()
    monkeypatch.delenv("PHARMA_ROOT", raising=False)
    monkeypatch.chdir(nested)
    assert setup.repo_root() == root


REPO_ROOT = Path(__file__).resolve().parents[1]


def _upgrades(monkeypatch) -> list[str]:
    """Record Alembic upgrades instead of touching a database."""
    from alembic import command

    calls: list[str] = []
    monkeypatch.setattr(command, "upgrade", lambda cfg, rev: calls.append(rev))
    return calls


def test_migrate_refuses_a_database_from_a_newer_version(monkeypatch):
    """A revision this code doesn't know stops boot with a plain message."""
    calls = _upgrades(monkeypatch)
    monkeypatch.setattr(setup, "database_revision", lambda: "0099_from_the_future")
    with pytest.raises(SystemExit) as stopped:
        setup.migrate(REPO_ROOT)
    message = str(stopped.value)
    assert "0099_from_the_future" in message
    assert "only move forward" in message
    assert "docker compose --profile app down -v" in message
    assert calls == []


def test_migrate_upgrades_a_known_or_fresh_database(monkeypatch):
    """A known revision, or none at all, is upgraded to head as before."""
    calls = _upgrades(monkeypatch)
    for revision in ("0006_review_problem", "0007_v2_rule_ids", None):
        monkeypatch.setattr(setup, "database_revision", lambda rev=revision: rev)
        setup.migrate(REPO_ROOT)
    assert calls == ["head", "head", "head"]
