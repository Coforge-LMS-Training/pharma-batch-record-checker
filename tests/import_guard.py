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
    path, is_package = _module_path(src_root, module_name)
    parts = module_name.split(".")
    # Relative imports resolve against the module's package; for a package's
    # own __init__.py, that package is the module itself.
    package_parts = parts if is_package else parts[:-1]
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


def _module_path(src_root: Path, module_name: str) -> tuple[Path, bool]:
    """The source file of a module: `name.py`, or `name/__init__.py` for a package."""
    base = src_root / module_name.replace(".", "/")
    if base.with_suffix(".py").is_file():
        return base.with_suffix(".py"), False
    if (base / "__init__.py").is_file():
        return base / "__init__.py", True
    raise FileNotFoundError(f"no source for module {module_name} under {src_root}")


def _parent_packages(module_name: str) -> list[str]:
    """Packages whose __init__ runs when module_name is imported (outermost first)."""
    parts = module_name.split(".")
    return [".".join(parts[:i]) for i in range(1, len(parts))]


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
    Importing a module also runs its parent packages' __init__.py, so those are
    always checked too.
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
        queue += [p for p in _parent_packages(current) if p.split(".")[0] == PROJECT]
        for name in module_imports(src_root, current):
            top = name.split(".")[0]
            if top in sys.stdlib_module_names or top in extra:
                continue
            if top == PROJECT and name in allowed_project:
                queue.append(name)
            else:
                found.add(name)
    return found


def is_within(name: str, package: str) -> bool:
    """True if `name` is `package` or a module inside it (dot boundary)."""
    return name == package or name.startswith(package + ".")


def project_modules(
    src_root: Path, package: str, exclude: Iterable[str] = ()
) -> list[str]:
    """All modules of `package` found on disk, recursing into subpackages.

    Read from the source tree (nothing is imported). Modules within any
    package in `exclude` are skipped. Sorted, package itself first.
    """
    excluded = tuple(exclude)
    base = src_root / package.replace(".", "/")
    found = []
    for path in base.rglob("*.py"):
        parts = list(path.relative_to(src_root).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        name = ".".join(parts)
        if not any(is_within(name, ex) for ex in excluded):
            found.append(name)
    return sorted(found)
