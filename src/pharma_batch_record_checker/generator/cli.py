"""generate-batch-records: one reproducible command for the synthetic dataset (PHA-38).

    generate-batch-records --seed 0 --batches 10 --start 2026-10-12T07:00 --out data/

Writes master_record.csv, batch_record.csv and expected_findings.csv to --out.
Each product line runs its batches back to back from --start, and every batch
ID takes its two-digit year from its start date. --catalog DIR adds extra
synthetic products from a folder of TOML templates, defect plans and products
(see catalog.py and catalog/README.md) after SYN500 and PLC250, or instead of
them with --no-builtin; without --catalog, only SYN500 and PLC250 are
generated. The same --seed, --batches, --start and catalog always give
byte-identical files; --seed changes readings only.
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
from pharma_batch_record_checker.generator.catalog import (
    Catalog,
    CatalogProduct,
    load_catalog,
)
from pharma_batch_record_checker.generator.dataset_io import (
    BATCH_FILE,
    EXPECTED_FILE,
    MASTER_FILE,
    write_dataset,
)
from pharma_batch_record_checker.generator.defects import (
    BATCH_PLANS,
    BatchPlan,
    Dataset,
    build_dataset,
    plant_defects,
)
from pharma_batch_record_checker.generator.expected_findings import (
    RULE_IDS,
    DefectLog,
)
from pharma_batch_record_checker.generator.masters import (
    PLC250,
    SYN500,
    ProductConfig,
    build_master,
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
    catalog_products: Sequence[CatalogProduct],
    base: Sequence[ProductLine] | None = None,
) -> tuple[ProductLine, ...]:
    """Product lines for the products of a --catalog folder, in catalog order.

    base is the built-in lines (None means PRODUCT_LINES). A catalog product's
    ID letter and master (id and version) must be new. Its defect plan, if
    any, becomes the line's plan and is dry-run planted on a clean batch, so
    a defect its limits can't take (e.g. out_of_range below a missing min) or
    a combination that couldn't all fire is refused here rather than mid-run.
    The catalog has already checked that the plan fits the product's
    template. Raises ValueError naming the problem.
    """
    base = PRODUCT_LINES if base is None else tuple(base)
    letters = {line.prefix for line in base}
    masters = {_master_id(line.product) for line in base}
    lines = []
    for item in catalog_products:
        name = _master_id(item.product)
        if item.prefix in letters:
            raise ValueError(f"{name}: ID letter {item.prefix} is already used")
        if name in masters:
            raise ValueError(f"{name} is already a product")
        letters.add(item.prefix)
        masters.add(name)
        plan = None if item.defect_plan is None else _checked_plan(item)
        lines.append(ProductLine(item.product, item.prefix, plan))
    return tuple(lines)


def _master_id(product: ProductConfig) -> str:
    """'MBR-X v1': how a master is named in messages and compared for clashes."""
    return f"{product.master_record_id} v{product.master_version}"


def _checked_plan(item: CatalogProduct) -> BatchPlan:
    """item's catalog plan as a BatchPlan, after a dry run on a clean batch."""
    spec = item.defect_plan
    plan = BatchPlan(
        item.product,
        f"{item.prefix}00-0000",  # placeholder; plan_batches sets the real ID
        DEFAULT_START,
        defects=spec.defects,
        comments=spec.comments,
    )
    master = build_master(item.product)
    clean = generate_clean_batch(master, plan.batch_id, plan.start, 0)
    try:
        plant_defects(master, clean, plan.defects, DefectLog(), plan.comments)
    except ValueError as error:
        raise ValueError(
            f"{_master_id(item.product)}: its defect plan can't be planted: {error}"
        ) from None
    return plan


def _catalog(text: str) -> Catalog:
    """argparse type for --catalog: the loaded catalog folder.

    Its lines are built in main, once --no-builtin is known, because the
    clash checks depend on which built-in lines are in use.
    """
    try:
        return load_catalog(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from None


def product_lines(catalog: Catalog | None, no_builtin: bool) -> tuple[ProductLine, ...]:
    """The product lines a run alternates: built-ins (unless left out), then catalog.

    Raises ValueError if a catalog product clashes with a line in use or its
    defect plan can't be planted (see extra_lines).
    """
    base = () if no_builtin else PRODUCT_LINES
    if catalog is None:
        return base
    return base + extra_lines(catalog.products, base)


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
    """The command-line options: --seed, --batches, --start, --catalog, --no-builtin, --out."""
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
        "--catalog",
        type=_catalog,
        default=None,
        metavar="DIR",
        help=(
            "folder of TOML templates, defect plans and products (see "
            "catalog/README.md); each product becomes a line after B and P, "
            "in file-name order. Without it, only the two built-in products. "
            "With k lines, --batches k+1 or more covers all 8 rules"
        ),
    )
    parser.add_argument(
        "--no-builtin",
        action="store_true",
        help=(
            "with --catalog: generate only the catalog's products, leaving out "
            "MBR-SYN500 (B) and MBR-PLC250 (P); their letters and masters are "
            "then free for catalog products"
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="output folder (created if needed; the three CSVs are replaced)",
    )
    return parser


def summarize(dataset: Dataset, out: Path, line_count: int | None = None) -> str:
    """What was written, for people: the first line, then the details.

    Lists each product line (letter, master, name, batches, defective
    batches), findings by severity and by rule (naming rules not planted),
    the span of timestamps, and the files. line_count is how many product
    lines the run alternated (default: the masters present); it sizes the
    --batches hint when no defects were planted. The text depends only on
    the dataset and out, so the same run always prints the same summary.
    """
    batch_ids = list(dict.fromkeys(row.batch_id for row in dataset.batches))
    first = (
        f"wrote {len(batch_ids)} batches, {len(dataset.findings)} expected "
        f"findings to {out}"
    )
    lines = [
        first,
        "  products:",
        *_product_lines(dataset),
        _findings_line(dataset, line_count),
    ]
    if dataset.findings:
        lines.append(_rules_line(dataset))
    stamps = [
        stamp
        for row in dataset.batches
        for stamp in (row.performed_at, row.verified_at)
        if stamp is not None
    ]
    if stamps:
        lines.append(
            f"  time span: {min(stamps):{TIMESTAMP_FORMAT}} to "
            f"{max(stamps):{TIMESTAMP_FORMAT}}"
        )
    lines.append(f"  files: {MASTER_FILE}, {BATCH_FILE}, {EXPECTED_FILE}")
    return "\n".join(lines)


def _product_lines(dataset: Dataset) -> list[str]:
    """One aligned line per master: letter, id and version, name, batch counts."""
    names: dict[tuple[str, str], str] = {}
    for row in dataset.masters:
        names.setdefault((row.master_record_id, row.master_version), row.product_name)
    batches: dict[tuple[str, str], list[str]] = {key: [] for key in names}
    for row in dataset.batches:
        ids = batches.setdefault((row.master_record_id, row.master_version), [])
        if row.batch_id not in ids:
            ids.append(row.batch_id)
    defective = {f.batch_id for f in dataset.findings}
    rows = [
        (
            ids[0][0] if ids else "?",
            f"{master_id} v{version}",
            names[(master_id, version)],
            (
                f"{len(ids)} batch{'' if len(ids) == 1 else 'es'}, "
                f"{sum(b in defective for b in ids)} defective"
            ),
        )
        for (master_id, version), ids in batches.items()
    ]
    id_width = max(len(r[1]) for r in rows)
    name_width = max(len(r[2]) for r in rows)
    return [
        f"    {letter}  {master:<{id_width}}  {name:<{name_width}}  {counts}"
        for letter, master, name, counts in rows
    ]


def _findings_line(dataset: Dataset, line_count: int | None) -> str:
    """Findings by severity and the steps they are on, or why there are none."""
    findings = dataset.findings
    if not findings:
        lines = line_count or len(
            {(m.master_record_id, m.master_version) for m in dataset.masters}
        )
        return (
            "  findings: none (a line's defects start at its 2nd batch; "
            f"use --batches {lines + 1} or more)"
        )
    critical = sum(f.severity == "Critical" for f in findings)
    steps = len({(f.batch_id, f.step_id) for f in findings})
    return (
        f"  findings: {critical} Critical, {len(findings) - critical} Needs Review, "
        f"on {steps} steps"
    )


def _rules_line(dataset: Dataset) -> str:
    """Findings per rule in the agreed rule order, and which rules have none."""
    counts = {rule: 0 for rule in RULE_IDS}
    for finding in dataset.findings:
        counts[finding.rule_id] += 1
    listed = ", ".join(f"{rule} {n}" for rule, n in counts.items() if n)
    missing = [rule for rule, n in counts.items() if not n]
    note = (
        f"all {len(RULE_IDS)} planted"
        if not missing
        else f"not planted: {', '.join(missing)}"
    )
    return f"  rules: {listed} ({note})"


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command and return its exit code.

    0 on success, 1 if the files can't be written (with a one-line error),
    and argparse exits with 2 on bad options (including a catalog product
    that clashes with a line in use or can't take its defect plan).
    """
    parser = _parser()
    args = parser.parse_args(argv)
    if args.no_builtin and args.catalog is None:
        parser.error("--no-builtin needs --catalog, or there is nothing to generate")
    try:
        lines = product_lines(args.catalog, args.no_builtin)
    except ValueError as error:
        parser.error(f"argument --catalog: {error}")
    dataset = build_dataset(args.seed, plan_batches(args.batches, args.start, lines))
    try:
        write_dataset(dataset, args.out)
    except OSError as error:
        print(
            f"generate-batch-records: error: cannot write to {args.out}: {error}",
            file=sys.stderr,
        )
        return 1
    print(summarize(dataset, args.out, len(lines)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
