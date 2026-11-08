"""generate-batch-records: one reproducible command for the synthetic dataset (PHA-38).

    generate-batch-records --seed 0 --batches 10 --out data/

Writes master_record.csv, batch_record.csv and expected_findings.csv to --out.
The same --seed and --batches always give byte-identical files. It needs no
rules engine and no database: everything comes from the generator modules.
"""

import dataclasses
from datetime import datetime, timedelta

from pharma_batch_record_checker.generator.defects import BATCH_PLANS, BatchPlan

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
