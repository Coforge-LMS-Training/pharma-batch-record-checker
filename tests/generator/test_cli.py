"""The seeded generator command, generate-batch-records (PHA-38)."""

from datetime import datetime

import pytest

from pharma_batch_record_checker.generator.cli import plan_batches
from pharma_batch_record_checker.generator.defects import build_dataset
from pharma_batch_record_checker.generator.expected_findings import RULE_IDS

# --- Planning N batches -------------------------------------------------------


def ids(plans):
    """Batch IDs of the plans, in order."""
    return [p.batch_id for p in plans]


def test_plans_alternate_products():
    """Batches alternate between the tablet and the capsule master."""
    products = [p.product.master_record_id for p in plan_batches(6)]
    assert products == ["MBR-SYN500", "MBR-PLC250"] * 3


@pytest.mark.parametrize("n", [1, 2, 5, 12])
def test_batch_count_matches_n(n):
    """--batches N gives exactly N batches."""
    assert len(plan_batches(n)) == n


def test_batch_ids_number_per_product():
    """Each product counts its own batches: B26-0001, P26-0001, B26-0002, ..."""
    assert ids(plan_batches(5)) == [
        "B26-0001",
        "P26-0001",
        "B26-0002",
        "P26-0002",
        "B26-0003",
    ]


def test_defects_on_every_other_batch_per_product():
    """Within a product, batches alternate clean and defective, starting clean."""
    defective = [bool(p.defects) for p in plan_batches(8)]
    assert defective == [False, False, True, True, False, False, True, True]


def test_four_batches_cover_all_eight_rules():
    """Already at N=4 the dataset plants every agreed rule."""
    dataset = build_dataset(0, plan_batches(4))
    assert {f.rule_id for f in dataset.findings} == set(RULE_IDS)


def test_start_times_are_deterministic():
    """A product's k-th batch starts at 07:00 on day k, from 2026-10-12."""
    starts = {p.batch_id: p.start for p in plan_batches(4)}
    assert starts == {
        "B26-0001": datetime(2026, 10, 12, 7, 0),
        "P26-0001": datetime(2026, 10, 12, 7, 0),
        "B26-0002": datetime(2026, 10, 13, 7, 0),
        "P26-0002": datetime(2026, 10, 13, 7, 0),
    }


@pytest.mark.parametrize("n", [0, -1, 10000])
def test_rejects_n_outside_range(n):
    """N must be 1 to 9999 (batch IDs have four digits per product)."""
    with pytest.raises(ValueError, match="batches"):
        plan_batches(n)
