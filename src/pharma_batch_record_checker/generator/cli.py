"""generate-batch-records: one reproducible command for the synthetic dataset (PHA-38).

    generate-batch-records --seed 0 --batches 10 --start 2026-10-12T07:00 --out data/

Writes master_record.csv, batch_record.csv and expected_findings.csv to --out.
Each product line runs its batches back to back from --start, and every batch
ID takes its two-digit year from its start date. The same --seed, --batches
and --start always give byte-identical files; --seed changes readings only.
It needs no rules engine and no database: everything comes from the generator
modules.
"""

import argparse
import dataclasses
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from pharma_batch_record_checker.generator.batches import (
    STEP_INTERVAL,
    VERIFY_DELAY_MINUTES,
    generate_clean_batch,
)
from pharma_batch_record_checker.generator.dataset_io import write_dataset
from pharma_batch_record_checker.generator.defects import (
    BATCH_PLANS,
    BatchPlan,
    build_dataset,
    plant_defects,
)
from pharma_batch_record_checker.generator.expected_findings import DefectLog
from pharma_batch_record_checker.generator.masters import (
    PLC250,
    SYN500,
    ProductConfig,
    build_master,
)
from pharma_batch_record_checker.generator.product_file import (
    FileProduct,
    load_products,
)
from pharma_batch_record_checker.schema import TIMESTAMP_FORMAT


@dataclass(frozen=True)
class ProductLine:
    """A product the command generates batches for, and how to name them.

    prefix is the letter that starts every batch ID of the product; the
    two-digit year and the sequence number follow it (B26-0001, ...).
    defect_plan is the plan whose defects every defective batch reuses; None
    means the product only gets clean batches.
    """

    product: ProductConfig
    prefix: str
    defect_plan: BatchPlan | None


def _defect_plan_for(product: ProductConfig) -> BatchPlan | None:
    """The product's PHA-37 defect plan from BATCH_PLANS, if it has one."""
    return next((p for p in BATCH_PLANS if p.product == product and p.defects), None)


# Batches alternate between these lines, in this order.
PRODUCT_LINES: tuple[ProductLine, ...] = (
    ProductLine(SYN500, "B", _defect_plan_for(SYN500)),
    ProductLine(PLC250, "P", _defect_plan_for(PLC250)),
)

# When each line's first batch starts, unless --start says otherwise.
DEFAULT_START = datetime(2026, 10, 12, 7, 0)
# A sanity limit on --batches. Batch IDs follow their start dates, so this
# no longer keeps anything in step; it only bounds the run size.
MAX_BATCHES = 9999

# Time between one batch's last possible timestamp and the next batch's first
# step on the same line (cleaning and line clearance).
CHANGEOVER = timedelta(hours=3)


def batch_span(product: ProductConfig) -> timedelta:
    """Longest time from a batch's first step to its last timestamp.

    The last step is performed (steps - 1) step intervals after the first, and
    may be verified up to the longest verification delay after that.
    """
    last_step = (len(product.process) - 1) * STEP_INTERVAL
    return last_step + timedelta(minutes=max(VERIFY_DELAY_MINUTES))


def check_batch_count(n: int) -> int:
    """Return n if it is a valid --batches value (1 to MAX_BATCHES), else raise."""
    if not 1 <= n <= MAX_BATCHES:
        raise ValueError(f"batches must be 1 to {MAX_BATCHES}, got {n}")
    return n


def check_start_year(when: datetime) -> datetime:
    """Return when if its year is 2000-2099, else raise.

    Batch IDs carry a two-digit year, which only names a year unambiguously
    within one century.
    """
    if not 2000 <= when.year <= 2099:
        raise ValueError(
            f"start year must be 2000 to 2099, got {when:{TIMESTAMP_FORMAT}}"
        )
    return when


def parse_start(text: str) -> datetime:
    """Parse a --start value: canonical YYYY-MM-DDTHH:MM, year 2000-2099.

    Like the CSV contract, non-canonical input (seconds, a timezone, a space
    separator, missing zero padding) is rejected, not normalized.
    """
    try:
        when = datetime.strptime(text, TIMESTAMP_FORMAT)
    except ValueError:
        when = None
    if when is None or when.strftime(TIMESTAMP_FORMAT) != text:
        raise ValueError(f"start must be YYYY-MM-DDTHH:MM, got {text!r}")
    return check_start_year(when)


def plan_batches(
    n: int,
    start: datetime = DEFAULT_START,
    lines: Sequence[ProductLine] | None = None,
) -> tuple[BatchPlan, ...]:
    """Plan n batches: alternate product lines; a line's even positions are defective.

    Each line's first batch starts at start, and each later one starts
    batch_span + CHANGEOVER after the previous batch on that line, so batches
    on a line never overlap and start times move around the clock. The
    schedule doesn't depend on the seed.

    A batch's ID is <prefix><YY>-<seq:04d>, where YY is its start year, and
    seq counts the line's batches within that year (B26-0001, B26-0002, ...,
    B27-0001). A batch is clean at odd positions on its line and carries the
    line's defect plan at even positions (lines without one stay clean). The
    position keeps counting across a new year, so where the year changes
    doesn't change which batches are defective. B26-0002 carries the full
    tablet plan, so n >= 3 plants all eight rules. Every batch must start in
    2000-2099 (see check_start_year).

    lines are the product lines to alternate, in order; None means
    PRODUCT_LINES (read at call time).
    """
    check_batch_count(n)
    check_start_year(start)
    lines = PRODUCT_LINES if lines is None else tuple(lines)
    next_start = [start] * len(lines)  # per line, by index
    seq_in_year: list[tuple[int, int]] = [(start.year, 0)] * len(lines)
    plans = []
    for i in range(n):
        index = i % len(lines)
        line = lines[index]
        position = i // len(lines) + 1
        batch_start = check_start_year(next_start[index])
        next_start[index] = batch_start + batch_span(line.product) + CHANGEOVER
        year, seq = seq_in_year[index]
        seq = seq + 1 if batch_start.year == year else 1
        seq_in_year[index] = (batch_start.year, seq)
        batch_id = f"{line.prefix}{batch_start:%y}-{seq:04d}"
        if position % 2 == 0 and line.defect_plan is not None:
            plans.append(
                dataclasses.replace(
                    line.defect_plan, batch_id=batch_id, start=batch_start
                )
            )
        else:
            plans.append(BatchPlan(line.product, batch_id, batch_start))
    return tuple(plans)


def extra_lines(
    file_products: Sequence[FileProduct],
    base: Sequence[ProductLine] | None = None,
) -> tuple[ProductLine, ...]:
    """Product lines for products read from a --products file, in file order.

    base is the built-in lines (None means PRODUCT_LINES). A file product's
    ID letter and master (id and version) must be new. defects_like must name
    a base product that has a defect plan and the same process template; the
    plan is copied onto the new product and dry-run planted, so a defect its
    limits can't take (e.g. out_of_range below a missing min) is refused here
    rather than mid-run. Raises ValueError naming the problem.
    """
    base = PRODUCT_LINES if base is None else tuple(base)
    letters = {line.prefix for line in base}
    masters = {_master_id(line.product) for line in base}
    plans = {line.product.master_record_id: line for line in base}
    lines = []
    for item in file_products:
        product, name = item.product, _master_id(item.product)
        if item.prefix in letters:
            raise ValueError(f"{name}: ID letter {item.prefix} is already used")
        if name in masters:
            raise ValueError(f"{name} is already a product")
        letters.add(item.prefix)
        masters.add(name)
        plan = None
        if item.defects_like is not None:
            plan = _reused_plan(item, plans.get(item.defects_like))
        lines.append(ProductLine(product, item.prefix, plan))
    return tuple(lines)


def _master_id(product: ProductConfig) -> str:
    """'MBR-X v1': how a master is named in messages and compared for clashes."""
    return f"{product.master_record_id} v{product.master_version}"


def _reused_plan(item: FileProduct, source: ProductLine | None) -> BatchPlan:
    """The source line's defect plan, moved onto item's product and dry-run planted."""
    name = _master_id(item.product)
    if source is None or source.defect_plan is None:
        raise ValueError(
            f"{name}: defects_like {item.defects_like} is not a built-in product "
            "with a defect plan"
        )
    if source.product.process != item.product.process:
        raise ValueError(
            f"{name}: defects_like {item.defects_like} uses a different process "
            "template, so its defects don't fit"
        )
    plan = dataclasses.replace(source.defect_plan, product=item.product)
    master = build_master(item.product)
    clean = generate_clean_batch(master, f"{item.prefix}00-0000", DEFAULT_START, 0)
    try:
        plant_defects(master, clean, plan.defects, DefectLog(), plan.comments)
    except ValueError as error:
        raise ValueError(
            f"{name}: defects from {item.defects_like} can't be planted: {error}"
        ) from None
    return plan


def _products_file(text: str) -> tuple[ProductLine, ...]:
    """argparse type for --products: the extra lines a products file describes."""
    try:
        return extra_lines(load_products(text))
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from None


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


def _start_time(text: str) -> datetime:
    """argparse type for --start: a timestamp accepted by parse_start."""
    try:
        return parse_start(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from None


def _parser() -> argparse.ArgumentParser:
    """The command-line options: --seed, --batches, --start, --products and --out."""
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
        "--start",
        type=_start_time,
        default=DEFAULT_START,
        help=(
            "when each product line's first batch starts, as YYYY-MM-DDTHH:MM "
            f"(default {DEFAULT_START:{TIMESTAMP_FORMAT}}); batch IDs take "
            "their year from their start date"
        ),
    )
    parser.add_argument(
        "--products",
        type=_products_file,
        default=(),
        metavar="FILE",
        help=(
            "TOML file of extra synthetic products (see examples/products.toml); "
            "their lines follow B and P in file order. Without it, only the two "
            "built-in products. With k lines, --batches k+1 or more covers all 8 rules"
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
    dataset = build_dataset(
        args.seed, plan_batches(args.batches, args.start, PRODUCT_LINES + args.products)
    )
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
