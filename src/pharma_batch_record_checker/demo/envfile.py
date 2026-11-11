"""Load KEY=VALUE lines from a .env file into the environment.

Only for the demo command. Variables already set in the shell win, so a .env
never overrides them. Blank lines and lines starting with # are skipped, and
matching quotes around a value are removed. .env is gitignored, so local
settings such as CONTACT_EMAIL stay out of the repository.
"""

import os
from pathlib import Path


def load_env_file(path: Path) -> list[str]:
    """Set unset variables from path. Returns the names that were set."""
    if not path.is_file():
        return []
    loaded = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded
