"""The app never imports the synthetic data generator.

The generator (owner: Sarthak) may use the shared CSV contract, but app code
(parser, rules engine, store, export, CLI) must not depend on the generator,
even indirectly through other app modules or nested app subpackages.
"""

import pytest

from tests.import_guard import SRC_ROOT, disallowed_imports, is_within, project_modules

PACKAGE = "pharma_batch_record_checker"
GENERATOR = f"{PACKAGE}.generator"
APP_MODULES = project_modules(SRC_ROOT, PACKAGE, exclude={GENERATOR})


def test_app_modules_are_discovered():
    """Discovery finds the package and the shared contract modules."""
    assert {PACKAGE, f"{PACKAGE}.schema", f"{PACKAGE}.csv_io"} <= set(APP_MODULES)


@pytest.mark.parametrize("module", APP_MODULES)
def test_app_modules_do_not_import_generator(module):
    """No app module reaches the generator package, directly or transitively."""
    reached = disallowed_imports(module, set(APP_MODULES), extra_allowed=())
    assert {name for name in reached if is_within(name, GENERATOR)} == set()
