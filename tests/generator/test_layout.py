"""The data generator lives in its own subpackage, apart from the shared contract.

Generator code (masters, clean batches, defects, expected findings) is in
`pharma_batch_record_checker.generator`. The CSV contract (`schema`, `csv_io`)
stays at the top level because the parser, store and export use it too.
"""

import importlib
from pathlib import Path

import pytest

from tests.generator import KNOWN_GENERATOR_MODULES

PACKAGE = "pharma_batch_record_checker"


@pytest.mark.parametrize("name", KNOWN_GENERATOR_MODULES)
def test_old_top_level_modules_are_gone(name):
    """No stale copy of a generator module is left at the top level.

    The missing module must be the old module itself: a stale file that fails
    on one of its own imports would also raise ModuleNotFoundError.
    """
    with pytest.raises(ModuleNotFoundError) as excinfo:
        importlib.import_module(f"{PACKAGE}.{name}")
    assert excinfo.value.name == f"{PACKAGE}.{name}"


@pytest.mark.parametrize("name", ["schema", "csv_io"])
def test_contract_stays_top_level(name):
    """The shared CSV contract lives directly in the package, not in generator/."""
    package_dir = Path(importlib.import_module(PACKAGE).__file__).parent
    module = importlib.import_module(f"{PACKAGE}.{name}")
    assert Path(module.__file__).parent == package_dir
