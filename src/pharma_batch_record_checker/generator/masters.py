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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType

from pydantic import ValidationError

from pharma_batch_record_checker.csv_io import Destination, write_master_csv
from pharma_batch_record_checker.schema import MasterRow


@dataclass(frozen=True)
class StepTemplate:
    """One step of a process template.

    unit_procedure, operation, phase and parameter may contain "{excipient}",
    filled in from the product; any other braces are kept as written. unit is
    never filled in, because units are compared as exact strings.

    A measured step has both a parameter and a unit (non-empty text). A
    sign-off-only step has both set to None.
    """

    step_id: str
    unit_procedure: str
    operation: str
    phase: str
    parameter: str | None
    unit: str | None
    critical: bool

    def __post_init__(self) -> None:
        """Reject '' and half-measured steps, so sign-off detection is unambiguous."""
        for name in ("parameter", "unit"):
            if getattr(self, name) == "":
                raise ValueError(f"{self.step_id}: {name} must be text or None, not ''")
        if (self.parameter is None) != (self.unit is None):
            raise ValueError(
                f"{self.step_id}: parameter and unit must both be set (measured "
                "step) or both be None (sign-off-only step)"
            )


@dataclass(frozen=True)
class ProductConfig:
    """A product's master: identity, batch size, process and per-step limits.

    `limits` maps each measured step_id to (min_value, max_value) as text,
    exactly as it should be written. "" means no limit on that side. The
    config keeps its own read-only copy, so approved limits can't be changed
    in place, and copies made with dataclasses.replace don't share it.
    """

    master_record_id: str
    master_version: str
    product_name: str
    batch_size: str
    batch_size_unit: str
    excipient: str
    process: tuple[StepTemplate, ...]
    limits: Mapping[str, tuple[str, str]] = field(default_factory=dict, hash=False)

    def __post_init__(self) -> None:
        """Freeze limits as a read-only copy of the mapping passed in."""
        object.__setattr__(self, "limits", MappingProxyType(dict(self.limits)))


def build_master(config: ProductConfig) -> list[MasterRow]:
    """Build a product's master rows, numbering steps 1..n in process order.

    Raises ValueError if the limits don't cover exactly the measured steps.
    """
    _check_process(config)
    _check_limits(config)
    return [
        _build_row(config, step_seq, step)
        for step_seq, step in enumerate(config.process, start=1)
    ]


def duplicate_step_ids(process: Sequence[StepTemplate]) -> list[str]:
    """step_ids used more than once in a process, sorted, each listed once."""
    ids = [s.step_id for s in process]
    return sorted({i for i in ids if ids.count(i) > 1})


def _check_process(config: ProductConfig) -> None:
    """step_id is part of the join key, so it must be unique within a process."""
    if duplicates := duplicate_step_ids(config.process):
        raise ValueError(f"{config.master_record_id}: duplicate step_ids {duplicates}")


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
    """Combine one process step with the product's identity and limits.

    A validation error is re-raised as ValueError naming the master and step,
    so a mistake in a new ProductConfig is easy to find.
    """
    min_value, max_value = config.limits.get(step.step_id, ("", ""))
    try:
        return _master_row(config, step_seq, step, min_value, max_value)
    except ValidationError as error:
        where = (
            f"{config.master_record_id} v{config.master_version} step {step.step_id}"
        )
        raise ValueError(f"{where}: {error}") from error


def _master_row(
    config: ProductConfig,
    step_seq: int,
    step: StepTemplate,
    min_value: str,
    max_value: str,
) -> MasterRow:
    """Construct the validated MasterRow for one step."""
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
        parameter="" if step.parameter is None else _fill(step.parameter, config),
        unit=step.unit or "",
        min_value=min_value,
        max_value=max_value,
        critical=step.critical,
    )


def _fill(text: str, config: ProductConfig) -> str:
    """Fill the {excipient} placeholder; other braces are left as written."""
    return text.replace("{excipient}", config.excipient)


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

# The approved masters, in the order they are written.
APPROVED_PRODUCTS: tuple[ProductConfig, ...] = (SYN500, PLC250)


def build_masters(
    products: Sequence[ProductConfig] = APPROVED_PRODUCTS,
) -> list[MasterRow]:
    """Master rows for each product, in order.

    By default these are the approved masters: MBR-SYN500 v3 (16 steps), then
    MBR-PLC250 v1 (10). Raises ValueError if two products share the same
    (master_record_id, master_version), which would duplicate the join key.
    """
    keys = [f"{p.master_record_id} v{p.master_version}" for p in products]
    if duplicates := sorted({k for k in keys if keys.count(k) > 1}):
        raise ValueError(f"duplicate masters {duplicates}")
    return [row for product in products for row in build_master(product)]


def write_masters(dest: Destination) -> None:
    """Write the approved masters to master_record.csv format at dest.

    Uses the shared PHA-33 writer: exact client header, CRLF, atomic write.
    """
    write_master_csv(build_masters(), dest)
