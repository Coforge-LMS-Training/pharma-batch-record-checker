"""The demo's .env loader. No database."""

import os

from pharma_batch_record_checker.demo.envfile import load_env_file


def test_env_file_sets_only_unset_values(tmp_path, monkeypatch):
    monkeypatch.setenv("KEEP_ME", "shell")
    monkeypatch.delenv("FROM_FILE", raising=False)
    monkeypatch.delenv("QUOTED", raising=False)
    env = tmp_path / ".env"
    env.write_text(
        '# comment\n\nKEEP_ME=file\nexport FROM_FILE=yes\nQUOTED="a b"\nbroken line\n',
        encoding="utf-8",
    )
    assert load_env_file(env) == ["FROM_FILE", "QUOTED"]
    assert os.environ["KEEP_ME"] == "shell"
    assert os.environ["FROM_FILE"] == "yes"
    assert os.environ["QUOTED"] == "a b"


def test_missing_env_file_is_fine(tmp_path):
    assert load_env_file(tmp_path / "missing") == []
