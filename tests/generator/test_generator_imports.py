"""Every generator module stays independent of the app (no rules engine, parser, store).

Modules are discovered recursively from the source tree, so a new generator
module or subpackage (e.g. a CLI) is checked automatically.
"""

import pytest

from tests.generator import KNOWN_GENERATOR_MODULES
from tests.import_guard import (
    SRC_ROOT,
    disallowed_imports,
    module_imports,
    project_modules,
)

GENERATOR = "pharma_batch_record_checker.generator"
GENERATOR_MODULES = project_modules(SRC_ROOT, GENERATOR)
ALLOWED_PROJECT = {
    *GENERATOR_MODULES,
    "pharma_batch_record_checker.schema",
    "pharma_batch_record_checker.csv_io",
}


def test_generator_modules_are_discovered():
    """Discovery finds the known modules (guards against an empty parametrize)."""
    assert {f"{GENERATOR}.{name}" for name in KNOWN_GENERATOR_MODULES} <= set(
        GENERATOR_MODULES
    )


@pytest.mark.parametrize("module", GENERATOR_MODULES)
def test_every_generator_module_is_engine_free(module):
    """A generator module may import only stdlib, pydantic, the contract and itself."""
    assert disallowed_imports(module, ALLOWED_PROJECT) == set()


def test_generator_init_imports_nothing():
    """Importing one generator module must not load the others via __init__."""
    assert module_imports(SRC_ROOT, GENERATOR) == set()
