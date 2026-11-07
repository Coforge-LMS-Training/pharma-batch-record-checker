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


# --- Process templates --------------------------------------------------------
# Columns: step_id, unit_procedure, operation, phase, parameter, unit, critical.
# Steps shared by both dosage forms are defined once.

_WEIGH_API = StepTemplate(
    "S-DISP-01", "Dispensing", "Weigh raw materials", "Weigh API",
    "API net weight", "kg", True,
)  # fmt: skip
_WEIGH_EXCIPIENT = StepTemplate(
    "S-DISP-02", "Dispensing", "Weigh raw materials", "Weigh {excipient}",
    "Excipient net weight", "kg", False,
)  # fmt: skip
_LINE_CLEARANCE = StepTemplate(
    "S-PACK-01", "Packaging", "Line setup", "Line clearance", None, None, True,
)  # fmt: skip

# Film-coated tablet, wet-granulation route (16 steps).
TABLET_PROCESS: tuple[StepTemplate, ...] = (
    _WEIGH_API,
    _WEIGH_EXCIPIENT,
    StepTemplate("S-GRAN-01", "Granulation", "Wet granulation", "Add granulating water", "Water added", "L", False),
    StepTemplate("S-GRAN-02", "Granulation", "Wet granulation", "Granulate", "Impeller speed", "rpm", False),
    StepTemplate("S-GRAN-03", "Granulation", "Wet granulation", "Granulate", "Granulation time", "min", False),
    StepTemplate("S-GRAN-04", "Granulation", "Fluid bed drying", "Dry granules", "Inlet air temperature", "degC", False),
    StepTemplate("S-GRAN-05", "Granulation", "Fluid bed drying", "Check dryness", "Loss on drying", "%", True),
    StepTemplate("S-BLEND-01", "Blending", "Final blend", "Blend", "Blend time", "min", False),
    StepTemplate("S-BLEND-02", "Blending", "Final blend", "Blend", "Blender speed", "rpm", False),
    StepTemplate("S-COMP-01", "Compression", "Tablet compression", "In-process check", "Tablet weight", "mg", True),
    StepTemplate("S-COMP-02", "Compression", "Tablet compression", "In-process check", "Tablet hardness", "kP", True),
    StepTemplate("S-COMP-03", "Compression", "Tablet compression", "In-process check", "Tablet thickness", "mm", False),
    StepTemplate("S-COAT-01", "Coating", "Film coating", "Spray coat", "Inlet air temperature", "degC", False),
    StepTemplate("S-COAT-02", "Coating", "Film coating", "Check coat", "Coating weight gain", "%", True),
    _LINE_CLEARANCE,
    StepTemplate("S-PACK-02", "Packaging", "Blister packaging", "Check label", "Label count reconciliation", "%", False),
)  # fmt: skip

# Hard capsule, dry-blend route (10 steps).
CAPSULE_PROCESS: tuple[StepTemplate, ...] = (
    _WEIGH_API,
    _WEIGH_EXCIPIENT,
    StepTemplate("S-BLEND-01", "Blending", "Pre-blend", "Blend", "Blend time", "min", False),
    StepTemplate("S-BLEND-02", "Blending", "Pre-blend", "Check dryness", "Loss on drying", "%", True),
    StepTemplate("S-BLEND-03", "Blending", "Lubrication", "Add magnesium stearate", "Lubrication time", "min", False),
    StepTemplate("S-ENC-01", "Encapsulation", "Capsule filling", "Set up encapsulator", "Room relative humidity", "%RH", False),
    StepTemplate("S-ENC-02", "Encapsulation", "Capsule filling", "In-process check", "Capsule fill weight", "mg", True),
    StepTemplate("S-ENC-03", "Encapsulation", "Polishing", "Polish and inspect", "Yield after inspection", "%", False),
    _LINE_CLEARANCE,
    StepTemplate("S-PACK-02", "Packaging", "Bottle filling", "Check count", "Capsules per bottle", "count", True),
)  # fmt: skip


# --- Approved products ----------------------------------------------------------

SYN500 = ProductConfig(
    master_record_id="MBR-SYN500",
    master_version="3",
    product_name="Synthetol 500 mg film-coated tablets",
    batch_size="300000",
    batch_size_unit="tablets",
    excipient="microcrystalline cellulose",
    process=TABLET_PROCESS,
    limits={
        "S-DISP-01": ("149.25", "150.75"),
        "S-DISP-02": ("29.85", "30.15"),
        "S-GRAN-01": ("18.0", "20.0"),
        "S-GRAN-02": ("150", "200"),
        "S-GRAN-03": ("4", "6"),
        "S-GRAN-04": ("55", "65"),
        "S-GRAN-05": ("1.0", "2.5"),
        "S-BLEND-01": ("10", "15"),
        "S-BLEND-02": ("8", "12"),
        "S-COMP-01": ("640", "680"),
        "S-COMP-02": ("12.0", "18.0"),
        "S-COMP-03": ("5.8", "6.2"),
        "S-COAT-01": ("55", "65"),
        "S-COAT-02": ("2.5", "3.5"),
        "S-PACK-02": ("99.5", "100.5"),
    },
)

PLC250 = ProductConfig(
    master_record_id="MBR-PLC250",
    master_version="1",
    product_name="Placeprol 250 mg hard capsules",
    batch_size="200000",
    batch_size_unit="capsules",
    excipient="lactose",
    process=CAPSULE_PROCESS,
    limits={
        "S-DISP-01": ("49.75", "50.25"),
        "S-DISP-02": ("19.90", "20.10"),
        "S-BLEND-01": ("15", "20"),
        "S-BLEND-02": ("", "3.0"),  # max only
        "S-BLEND-03": ("2", "4"),
        "S-ENC-01": ("35", "55"),
        "S-ENC-02": ("300", "330"),
        "S-ENC-03": ("98.0", ""),  # min only
        "S-PACK-02": ("30", "30"),  # exact count (inclusive limits)
    },
)
