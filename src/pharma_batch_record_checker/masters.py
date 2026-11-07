"""Template-driven generator for synthetic master batch records.

A master is built from two parts:

- a **process template**: an ordered tuple of `StepTemplate`s for one dosage
  form. It carries the ISA-88 hierarchy (unit procedure / operation / phase),
  what is measured, and which steps are critical;
- a **product config** (`ProductConfig`): the product's identity, batch size,
  excipient, and its limits for every measured step.

There is no randomness, so the same config always gives the same master.
All data is synthetic.
"""

from dataclasses import dataclass, field

from pharma_batch_record_checker.schema import MasterRow


@dataclass(frozen=True)
class StepTemplate:
    """One step of a process template.

    Text fields may contain "{excipient}", filled in from the product.
    A sign-off-only step has parameter and unit set to None.
    """

    step_id: str
    unit_procedure: str
    operation: str
    phase: str
    parameter: str | None
    unit: str | None
    critical: bool


@dataclass(frozen=True)
class ProductConfig:
    """A product's master: identity, batch size, process and per-step limits.

    `limits` maps each measured step_id to (min_value, max_value) as text,
    exactly as it should be written. "" means no limit on that side.
    """

    master_record_id: str
    master_version: str
    product_name: str
    batch_size: str
    batch_size_unit: str
    excipient: str
    process: tuple[StepTemplate, ...]
    limits: dict[str, tuple[str, str]] = field(default_factory=dict)


def build_master(config: ProductConfig) -> list[MasterRow]:
    """Build a product's master rows, numbering steps 1..n in process order.

    Raises ValueError if the limits don't cover exactly the measured steps.
    """
    _check_limits(config)
    return [
        _build_row(config, step_seq, step)
        for step_seq, step in enumerate(config.process, start=1)
    ]


def _check_limits(config: ProductConfig) -> None:
    """Limits must be given for every measured step and for nothing else."""
    measured = {s.step_id for s in config.process if s.parameter is not None}
    sign_off = {s.step_id for s in config.process if s.parameter is None}
    given = set(config.limits)
    problems = []
    if missing := measured - given:
        problems.append(f"no limits for measured steps {sorted(missing)}")
    if on_sign_off := given & sign_off:
        problems.append(f"limits given for sign-off-only steps {sorted(on_sign_off)}")
    if unknown := given - measured - sign_off:
        problems.append(f"limits given for unknown steps {sorted(unknown)}")
    if problems:
        raise ValueError(f"{config.master_record_id}: " + "; ".join(problems))


def _build_row(config: ProductConfig, step_seq: int, step: StepTemplate) -> MasterRow:
    """Combine one process step with the product's identity and limits."""
    min_value, max_value = config.limits.get(step.step_id, ("", ""))
    return MasterRow(
        master_record_id=config.master_record_id,
        master_version=config.master_version,
        product_name=config.product_name,
        batch_size=config.batch_size,
        batch_size_unit=config.batch_size_unit,
        step_seq=step_seq,
        step_id=step.step_id,
        unit_procedure=_fill(step.unit_procedure, config),
        operation=_fill(step.operation, config),
        phase=_fill(step.phase, config),
        parameter=_fill(step.parameter, config) if step.parameter else "",
        unit=step.unit or "",
        min_value=min_value,
        max_value=max_value,
        critical=step.critical,
    )


def _fill(text: str, config: ProductConfig) -> str:
    """Fill product placeholders such as {excipient} in template text."""
    return text.format(excipient=config.excipient)
