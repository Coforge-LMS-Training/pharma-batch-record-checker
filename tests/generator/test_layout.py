"""The data generator lives in its own subpackage, apart from the shared contract.

Generator code (masters, clean batches, defects, expected findings) is in
`pharma_batch_record_checker.generator`. The CSV contract (`schema`, `csv_io`)
stays at the top level because the parser, store and export use it too.
"""

import importlib

import pytest

GENERATOR_MODULES = ("masters", "batches", "defects", "expected_findings")
PACKAGE = "pharma_batch_record_checker"


@pytest.mark.parametrize("name", GENERATOR_MODULES)
def test_generator_modules_live_in_generator_package(name):
    """Each generator module is importable from the generator subpackage."""
    module = importlib.import_module(f"{PACKAGE}.generator.{name}")
    assert module.__name__ == f"{PACKAGE}.generator.{name}"


@pytest.mark.parametrize("name", GENERATOR_MODULES)
def test_old_top_level_modules_are_gone(name):
    """No stale copy of a generator module is left at the top level."""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(f"{PACKAGE}.{name}")


@pytest.mark.parametrize("name", ["schema", "csv_io"])
def test_contract_stays_top_level(name):
    """The shared CSV contract stays where every owner can import it."""
    assert importlib.import_module(f"{PACKAGE}.{name}").__name__ == f"{PACKAGE}.{name}"
