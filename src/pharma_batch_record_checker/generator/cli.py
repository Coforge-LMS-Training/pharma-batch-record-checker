"""generate-batch-records: one reproducible command for the synthetic dataset (PHA-38).

    generate-batch-records --seed 0 --batches 10 --out data/

Writes master_record.csv, batch_record.csv and expected_findings.csv to --out.
The same --seed and --batches always give byte-identical files. It needs no
rules engine and no database: everything comes from the generator modules.
"""

import argparse
import dataclasses
import sys
from collections.abc import Sequence
from datetime import datetime, timedelta
from pathlib import Path

from pharma_batch_record_checker.generator.dataset_io import write_dataset
from pharma_batch_record_checker.generator.defects import (
    BATCH_PLANS,
    BatchPlan,
    build_dataset,
)

# The products, in the order batches alternate between them.
PRODUCTS = tuple(dict.fromkeys(plan.product for plan in BATCH_PLANS))

# Each product's defect plan from PHA-37 (all 8 rules for the tablet), reused
# for every defective batch of that product with a new batch ID and start.
DEFECT_PLANS = {plan.product: plan for plan in BATCH_PLANS if plan.defects}

FIRST_START = datetime(2026, 10, 12, 7, 0)
MAX_BATCHES = 9999


def plan_batches(n: int) -> tuple[BatchPlan, ...]:
    """Plan n batches: alternate products; a product's even-numbered batches are defective.

    A product's k-th batch has ID <prefix>-<k:04d> (B26-0001, P26-0001, ...),
    starts at 07:00 on day k from 2026-10-12, and is clean when k is odd and
    carries that product's defect plan when k is even. So n >= 4 plants all
    eight rules.
    """
    if not 1 <= n <= MAX_BATCHES:
        raise ValueError(f"batches must be 1 to {MAX_BATCHES}, got {n}")
    plans = []
    for i in range(n):
        product = PRODUCTS[i % len(PRODUCTS)]
        k = i // len(PRODUCTS) + 1
        template = DEFECT_PLANS[product]
        prefix = template.batch_id.split("-")[0]
        batch_id = f"{prefix}-{k:04d}"
        start = FIRST_START + timedelta(days=k - 1)
        if k % 2 == 0:
            plans.append(dataclasses.replace(template, batch_id=batch_id, start=start))
        else:
            plans.append(BatchPlan(product, batch_id, start))
    return tuple(plans)


def _batch_count(text: str) -> int:
    """argparse type for --batches: a whole number from 1 to MAX_BATCHES."""
    try:
        n = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number: {text!r}") from None
    if not 1 <= n <= MAX_BATCHES:
        raise argparse.ArgumentTypeError(f"must be 1 to {MAX_BATCHES}, got {n}")
    return n


def _parser() -> argparse.ArgumentParser:
    """The command-line options: --seed, --batches and --out."""
    parser = argparse.ArgumentParser(
        prog="generate-batch-records",
        description=(
            "Write a reproducible synthetic dataset: master_record.csv, "
            "batch_record.csv and expected_findings.csv."
        ),
    )
    parser.add_argument(
        "--seed", type=int, default=0, help="seed for clean readings (default 0)"
    )
    parser.add_argument(
        "--batches",
        type=_batch_count,
        default=4,
        help=(
            "total batches, alternating products; every other batch per "
            "product has planted defects (default 4, which covers all 8 rules)"
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="output folder (created if needed; the three CSVs are replaced)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command; returns the exit code (argparse exits 2 on bad options)."""
    args = _parser().parse_args(argv)
    dataset = build_dataset(args.seed, plan_batches(args.batches))
    write_dataset(dataset, args.out)
    print(
        f"wrote {args.batches} batches, {len(dataset.findings)} expected findings "
        f"to {args.out}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
