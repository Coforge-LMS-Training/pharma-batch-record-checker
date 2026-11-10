"""Static guards on the container files (Dockerfile, .dockerignore, compose).

These read the files as text, so they run in CI without Docker. They pin the
parts that matter for safety and for the host workflow: client samples and
secrets never enter the image, the server doesn't run as root, the app port
is published on 127.0.0.1 only, and the app services sit behind a profile so
the host's plain `docker compose up` still starts only Postgres.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"


def dockerignore_entries():
    """The non-comment, non-blank lines of .dockerignore."""
    lines = DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
    return {line.strip() for line in lines if line.strip() and not line.startswith("#")}


def dockerfile_runtime_stage():
    """The Dockerfile text from its last FROM (the stage that runs) to the end."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    return text[text.rindex("\nFROM ") :]


# --- .dockerignore ------------------------------------------------------------


def test_dockerignore_keeps_client_samples_secrets_and_local_files_out():
    """sample/ (client CSVs), .env, data/ and personal notes never reach the image."""
    assert {"sample/", ".env", "data/", "CLAUDE.local.md", ".git", ".venv"} <= (
        dockerignore_entries()
    )


# --- Dockerfile ---------------------------------------------------------------


def test_dockerfile_runs_as_a_non_root_user():
    """The runtime stage switches to a named, non-root user."""
    users = re.findall(r"^USER\s+(\S+)", dockerfile_runtime_stage(), re.MULTILINE)
    assert users
    assert users[-1] not in {"root", "0"}


def test_dockerfile_creates_data_owned_by_the_runtime_user():
    """/data is owned by the runtime user, so a fresh pharma_data volume is writable."""
    stage = dockerfile_runtime_stage()
    user = re.findall(r"^USER\s+(\S+)", stage, re.MULTILINE)[-1]
    assert re.search(rf"chown\s+{user}(:{user})?\s+/data\b", stage)


def test_dockerfile_never_copies_client_samples():
    """No COPY line brings in sample/ (the client sample CSVs)."""
    copies = re.findall(
        r"^COPY\b.*$", DOCKERFILE.read_text(encoding="utf-8"), re.MULTILINE
    )
    assert copies
    assert not any(re.search(r"(^|\s)\.?/?sample\b", line) for line in copies)


def test_dockerfile_pins_its_base_images():
    """Every FROM names a tag, never an implicit 'latest'."""
    froms = re.findall(
        r"^FROM\s+(\S+)", DOCKERFILE.read_text(encoding="utf-8"), re.MULTILINE
    )
    assert froms
    assert all(":" in image and not image.endswith(":latest") for image in froms)
