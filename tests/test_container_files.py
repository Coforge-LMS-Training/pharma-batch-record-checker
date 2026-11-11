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


# --- docker-compose.yml -------------------------------------------------------

COMPOSE = ROOT / "docker-compose.yml"


def compose_block(name, top="services"):
    """The text of one entry under a top-level compose key (indented 2 spaces)."""
    text = COMPOSE.read_text(encoding="utf-8")
    section = re.search(rf"^{top}:\n(.*?)(?=^\S|\Z)", text, re.MULTILINE | re.DOTALL)
    assert section, f"no {top}: section"
    entry = re.search(
        rf"^  {name}:\n(.*?)(?=^  \S|\Z)", section.group(1), re.MULTILINE | re.DOTALL
    )
    assert entry, f"no {top}.{name}"
    return entry.group(1)


def test_postgres_has_no_profile_so_the_host_workflow_starts_only_it():
    """`uv run pharma-review-demo` runs plain `docker compose up`: only postgres."""
    assert "profiles:" not in compose_block("postgres")


def profiles_of(name):
    """The profiles a compose service declares, e.g. ['app'] (empty if none)."""
    found = re.search(r"profiles:\s*\[([^\]]*)\]", compose_block(name))
    return [p.strip().strip('"') for p in found.group(1).split(",")] if found else []


def test_app_and_generator_have_separate_profiles():
    """`--profile app up` boots the app only; the generator runs on its own.

    The generator is in the tools profile, so booting the app never rewrites
    pharma_data; `docker compose run generator` enables its profile itself.
    """
    assert profiles_of("app") == ["app"]
    assert profiles_of("generator") == ["tools"]


def test_app_port_is_published_on_localhost_only():
    """Notes have no CSRF protection, so the app is reachable from this machine only."""
    ports = re.findall(r'-\s*"([^"]+:8000)"', compose_block("app"))
    assert ports
    assert all(port.startswith("127.0.0.1:") for port in ports)


def test_data_volume_is_external_and_named_pharma_data():
    """pharma_data is created once and never removed by `down -v`.

    Compose doesn't own an external volume, so resetting the database keeps
    the generated data. Its Docker name is exactly pharma_data.
    """
    block = compose_block("pharma_data", "volumes")
    assert re.search(r"name:\s*pharma_data\b", block)
    assert re.search(r"external:\s*true\b", block)


def test_generator_writes_and_app_reads_pharma_data():
    """The generator mounts pharma_data read-write; the app mounts it read-only."""
    assert re.search(
        r"-\s*pharma_data:/data\s*$", compose_block("generator"), re.MULTILINE
    )
    assert re.search(
        r"-\s*pharma_data:/data:ro\s*$", compose_block("app"), re.MULTILINE
    )


def test_app_does_not_depend_on_the_generator():
    """Booting the app never runs the generator: it serves what pharma_data holds."""
    depends = re.search(r"depends_on:\n((?:\s{6,}.*\n?)*)", compose_block("app"))
    assert depends
    assert "generator" not in depends.group(1)
    assert "postgres" in depends.group(1)


def test_generator_defaults_to_15_batches():
    """`docker compose run --rm generator` makes 15 batches unless BATCHES is set.

    With the catalog there are 6 product lines, and a line's defective batch
    is its 2nd, so 12 batches reach every product's defects; 15 leaves room.
    """
    command = compose_block("generator")
    assert re.search(r'"--batches"\s*\n\s*-\s*"\$\{BATCHES:-15\}"', command)
