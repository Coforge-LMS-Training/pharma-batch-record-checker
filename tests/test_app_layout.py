"""The app never imports the synthetic data generator.

The generator (owner: Sarthak) may use the shared CSV contract, but app code
(parser, rules engine, store, export, CLI) must not depend on the generator,
even indirectly through other app modules.
"""

import pkgutil

import pytest

import pharma_batch_record_checker as package
from tests.import_guard import disallowed_imports

PACKAGE = "pharma_batch_record_checker"
GENERATOR = f"{PACKAGE}.generator"
APP_MODULES = sorted(
    [PACKAGE]
    + [
        f"{PACKAGE}.{info.name}"
        for info in pkgutil.iter_modules(package.__path__)
        if info.name != "generator"
    ]
)


@pytest.mark.parametrize("module", APP_MODULES)
def test_app_modules_do_not_import_generator(module):
    """No app module reaches the generator package, directly or transitively."""
    reached = disallowed_imports(module, set(APP_MODULES), extra_allowed=())
    assert {name for name in reached if name.startswith(GENERATOR)} == set()
