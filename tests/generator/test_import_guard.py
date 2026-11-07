"""Tests for the shared import guard used to keep generator code engine-free."""

from tests.import_guard import (
    disallowed_imports,
    is_within,
    module_imports,
    project_modules,
)

PKG = "pharma_batch_record_checker"


def make_package(root, files):
    """Write a tiny package under root: {module_name: source}."""
    package = root / PKG
    package.mkdir()
    (package / "__init__.py").write_text("")
    for name, source in files.items():
        (package / f"{name}.py").write_text(source)
    return root


def test_relative_import_is_resolved(tmp_path):
    """'from . import masters' inside the package means the package's masters."""
    root = make_package(tmp_path, {"masters": "", "defects": "from . import masters\n"})
    assert module_imports(root, f"{PKG}.defects") == {f"{PKG}.masters"}
    assert disallowed_imports(f"{PKG}.defects", {f"{PKG}.masters"}, root) == set()


def test_package_import_of_submodule_is_resolved(tmp_path):
    """'from pharma_batch_record_checker import masters' names the submodule."""
    root = make_package(
        tmp_path, {"masters": "", "defects": f"from {PKG} import masters\n"}
    )
    assert module_imports(root, f"{PKG}.defects") == {f"{PKG}.masters"}


def test_disallowed_import_found_transitively(tmp_path):
    """An allowed module that imports something forbidden is caught too."""
    root = make_package(
        tmp_path,
        {
            "masters": "import rules_engine\nimport json\n",
            "defects": "from .masters import thing\n",
        },
    )
    assert disallowed_imports(f"{PKG}.defects", {f"{PKG}.masters"}, root) == {
        "rules_engine"
    }


def test_project_module_outside_allowlist_is_flagged(tmp_path):
    """A project module that isn't on the allowlist is reported by name."""
    root = make_package(
        tmp_path, {"rules": "", "defects": "from .rules import check\n"}
    )
    assert disallowed_imports(f"{PKG}.defects", set(), root) == {f"{PKG}.rules"}


def test_stdlib_and_pydantic_are_allowed(tmp_path):
    """Standard library and pydantic imports are always fine."""
    root = make_package(
        tmp_path,
        {"defects": "import re\nfrom decimal import Decimal\nimport pydantic\n"},
    )
    assert disallowed_imports(f"{PKG}.defects", set(), root) == set()


def test_missing_submodule_is_named_precisely(tmp_path):
    """A relative import of a module that doesn't exist is still named in full."""
    root = make_package(tmp_path, {"defects": "from . import rules_engine\n"})
    assert disallowed_imports(f"{PKG}.defects", set(), root) == {f"{PKG}.rules_engine"}


# --- PHA-37 follow-up C: package __init__ files ------------------------------


def test_package_init_is_scanned(tmp_path):
    """Importing any submodule runs the package __init__, so it is checked too."""
    root = make_package(
        tmp_path, {"masters": "", "defects": "from .masters import thing\n"}
    )
    (root / PKG / "__init__.py").write_text("from .rules_engine import evaluate\n")
    assert disallowed_imports(f"{PKG}.defects", {f"{PKG}.masters"}, root) == {
        f"{PKG}.rules_engine"
    }


def test_package_module_reads_init(tmp_path):
    """A package on the allowlist is read from its __init__.py, without crashing."""
    root = make_package(tmp_path, {"defects": f"import {PKG}\n"})
    (root / PKG / "__init__.py").write_text("import json\n")
    assert disallowed_imports(f"{PKG}.defects", {PKG}, root) == set()


def test_relative_import_in_init_resolves_within_package(tmp_path):
    """'from . import masters' in the package __init__ means the package's masters."""
    root = make_package(tmp_path, {"masters": ""})
    (root / PKG / "__init__.py").write_text("from . import masters\n")
    assert module_imports(root, PKG) == {f"{PKG}.masters"}


# --- Recursive discovery and package boundaries -----------------------------


def test_project_modules_finds_nested_subpackages(tmp_path):
    """Modules in nested subpackages are found; excluded subtrees are skipped."""
    root = make_package(tmp_path, {"a": ""})
    sub = root / PKG / "sub"
    sub.mkdir()
    (sub / "__init__.py").write_text("")
    (sub / "b.py").write_text("")
    skip = root / PKG / "generator"
    skip.mkdir()
    (skip / "__init__.py").write_text("")
    (skip / "c.py").write_text("")
    assert project_modules(root, PKG, exclude={f"{PKG}.generator"}) == [
        PKG,
        f"{PKG}.a",
        f"{PKG}.sub",
        f"{PKG}.sub.b",
    ]
    assert project_modules(root, f"{PKG}.generator") == [
        f"{PKG}.generator",
        f"{PKG}.generator.c",
    ]


def test_generator_prefix_needs_dot_boundary():
    """A module is within a package only at a dot boundary."""
    assert is_within(f"{PKG}.generator", f"{PKG}.generator")
    assert is_within(f"{PKG}.generator.masters", f"{PKG}.generator")
    assert not is_within(f"{PKG}.generator_config", f"{PKG}.generator")
    assert not is_within(f"{PKG}.generators.x", f"{PKG}.generator")
