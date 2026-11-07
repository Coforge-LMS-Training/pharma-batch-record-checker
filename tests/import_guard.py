"""Test helper: prove generator modules never import a rules engine.

Expected findings must come from planted defects only. These helpers read a
module's source (without importing it), resolve relative imports, and follow
allowed project modules transitively, so a forbidden import can't hide one
module away.
"""

import ast
import sys
from collections.abc import Iterable
from pathlib import Path

PROJECT = "pharma_batch_record_checker"
SRC_ROOT = Path(__file__).resolve().parents[1] / "src"


def module_imports(src_root: Path, module_name: str) -> set[str]:
    """Absolute names of everything `module_name` imports.

    Relative imports are resolved against the module's package, and
    `from package import submodule` is reported as `package.submodule`.
    """
    path = src_root / (module_name.replace(".", "/") + ".py")
    package_parts = module_name.split(".")[:-1]
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text("utf-8"))):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = ".".join(package_parts[: len(package_parts) - node.level + 1])
                target = f"{base}.{node.module}" if node.module else base
            else:
                target = node.module or ""
            names |= _expand_package_import(src_root, target, node.names)
    return names


def _expand_package_import(
    src_root: Path, target: str, aliases: Iterable[ast.alias]
) -> set[str]:
    """`from pkg import name` is reported as `pkg.name` when pkg is a package.

    The name is reported even if no such module exists yet, so a forbidden
    import is named precisely (e.g. `pharma_batch_record_checker.rules_engine`).
    """
    if not (src_root / target.replace(".", "/")).is_dir():
        return {target}
    return {f"{target}.{a.name}" for a in aliases}


def disallowed_imports(
    module_name: str,
    allowed_project: set[str],
    src_root: Path = SRC_ROOT,
    extra_allowed: Iterable[str] = ("pydantic",),
) -> set[str]:
    """Imports reachable from `module_name` that are not allowed.

    Allowed: the standard library, `extra_allowed` packages, and the project
    modules in `allowed_project` (whose own imports are checked the same way).
    """
    extra = set(extra_allowed)
    found: set[str] = set()
    seen: set[str] = set()
    queue = [module_name]
    while queue:
        current = queue.pop()
        if current in seen:
            continue
        seen.add(current)
        for name in module_imports(src_root, current):
            top = name.split(".")[0]
            if top in sys.stdlib_module_names or top in extra:
                continue
            if top == PROJECT and name in allowed_project:
                queue.append(name)
            else:
                found.add(name)
    return found
