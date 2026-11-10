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
