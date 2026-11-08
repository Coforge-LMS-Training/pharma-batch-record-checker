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
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from pharma_batch_record_checker.generator.dataset_io import write_dataset
from pharma_batch_record_checker.generator.defects import (
    BATCH_PLANS,
    BatchPlan,
    build_dataset,
)
from pharma_batch_record_checker.generator.masters import (
    PLC250,
    SYN500,
    ProductConfig,
)


@dataclass(frozen=True)
class ProductLine:
    """A product the command generates batches for, and how to name them.

    prefix starts every batch ID of the product (B26-0001, ...). defect_plan is
    the plan whose defects every defective batch reuses; None means the
    product only gets clean batches.
    """

    product: ProductConfig
    prefix: str
    defect_plan: BatchPlan | None


def _defect_plan_for(product: ProductConfig) -> BatchPlan | None:
    """The product's PHA-37 defect plan from BATCH_PLANS, if it has one."""
    return next((p for p in BATCH_PLANS if p.product == product and p.defects), None)


# Batches alternate between these lines, in this order.
PRODUCT_LINES: tuple[ProductLine, ...] = (
    ProductLine(SYN500, "B26", _defect_plan_for(SYN500)),
    ProductLine(PLC250, "P26", _defect_plan_for(PLC250)),
)

FIRST_START = datetime(2026, 10, 12, 7, 0)
LAST_DAY = date(2026, 12, 31)
# One batch per product per day, and every batch must start in 2026 so its
# date matches the "26" in its ID.
DAYS_AVAILABLE = (LAST_DAY - FIRST_START.date()).days + 1
MAX_BATCHES = DAYS_AVAILABLE * len(PRODUCT_LINES)


def check_batch_count(n: int) -> int:
    """Return n if it is a valid --batches value (1 to MAX_BATCHES), else raise."""
    if not 1 <= n <= MAX_BATCHES:
        raise ValueError(f"batches must be 1 to {MAX_BATCHES}, got {n}")
    return n


def plan_batches(n: int) -> tuple[BatchPlan, ...]:
    """Plan n batches: alternate product lines; a line's even-numbered batches are defective.

    A line's k-th batch has ID <prefix>-<k:04d> (B26-0001, P26-0001, ...),
    starts at 07:00 on day k from 2026-10-12, and is clean when k is odd and
    carries the line's defect plan when k is even (lines without one stay
    clean). B26-0002 carries the full tablet plan, so n >= 3 plants all eight
    rules.
    """
    check_batch_count(n)
    lines = PRODUCT_LINES
    plans = []
    for i in range(n):
        line = lines[i % len(lines)]
        k = i // len(lines) + 1
        batch_id = f"{line.prefix}-{k:04d}"
        start = FIRST_START + timedelta(days=k - 1)
        if k % 2 == 0 and line.defect_plan is not None:
            plans.append(
                dataclasses.replace(line.defect_plan, batch_id=batch_id, start=start)
            )
        else:
            plans.append(BatchPlan(line.product, batch_id, start))
    return tuple(plans)


def _batch_count(text: str) -> int:
    """argparse type for --batches: a whole number accepted by check_batch_count."""
    try:
        n = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a whole number: {text!r}") from None
    try:
        return check_batch_count(n)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from None


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
            "product has planted defects (default 4; 3 or more covers all 8 rules)"
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
    """Run the command and return its exit code.

    0 on success, 1 if the files can't be written (with a one-line error),
    and argparse exits with 2 on bad options.
    """
    args = _parser().parse_args(argv)
    dataset = build_dataset(args.seed, plan_batches(args.batches))
    try:
        write_dataset(dataset, args.out)
    except OSError as error:
        print(
            f"generate-batch-records: error: cannot write to {args.out}: {error}",
            file=sys.stderr,
        )
        return 1
    print(
        f"wrote {args.batches} batches, {len(dataset.findings)} expected findings "
        f"to {args.out}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
