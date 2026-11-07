"""Normalized, immutable record shapes for the two approved CSV inputs.

The parser turns validated CSV rows (`MasterRow`, `BatchRow` from schema.py)
into these shapes; the rule engine works on them.

- A `MasterVersion` holds the header fields of one master record version once,
  plus its `MasterStep`s ordered by step_seq (ISA-88 hierarchy:
  unit_procedure > operation > phase).
- A `BatchStep` is one executed step. It is a `BatchRow` with a `step_key`:
  same fields, same validation, nothing added.
- A step is only ever looked up by `StepKey` (master_record_id,
  master_version, step_id), never by step_id alone: the same step_id exists in
  different masters and versions.

Values are kept exactly as validated by schema.py: a blank reading is None
(never 0), units keep their case and spelling, and comments are plain data.
There is no CSV reading, error collection or rule logic here.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType
from typing import NamedTuple

from pharma_batch_record_checker.schema import (
    BATCH_HEADER,
    BatchRow,
    MasterRow,
    _check_header,
)


class MasterKey(NamedTuple):
    """Identifies one version of one master record."""

    master_record_id: str
    master_version: str


class StepKey(NamedTuple):
    """Identifies one step of one master record version: the join key."""

    master_record_id: str
    master_version: str
    step_id: str


@dataclass(frozen=True)
class MasterStep:
    """One step of a master version, without the repeated header fields.

    A sign-off-only step (e.g. line clearance) has parameter, unit and both
    limits set to None. A None limit means no limit on that side.
    """

    step_seq: int
    step_id: str
    unit_procedure: str
    operation: str
    phase: str
    parameter: str | None
    unit: str | None
    min_value: Decimal | None
    max_value: Decimal | None
    critical: bool

    @classmethod
    def from_row(cls, row: MasterRow) -> "MasterStep":
        """Take the step fields of an already-validated master row as they are."""
        return cls(**{name: getattr(row, name) for name in _MASTER_STEP_FIELDS})


_MASTER_STEP_FIELDS: tuple[str, ...] = tuple(MasterStep.__dataclass_fields__)

# Header fields repeated on every master CSV row; stored once per version.
_MASTER_VERSION_FIELDS: tuple[str, ...] = (
    "master_record_id",
    "master_version",
    "product_name",
    "batch_size",
    "batch_size_unit",
)


def _seq_order(step: MasterStep) -> int:
    """Numeric sort key, so step 10 comes after step 2 whatever the type."""
    return int(step.step_seq)


@dataclass(frozen=True)
class MasterVersion:
    """One master record version: its header fields once, and its steps.

    Steps are stored as a tuple sorted numerically by step_seq. A duplicate
    step_id or step_seq within the version raises ValueError.
    """

    master_record_id: str
    master_version: str
    product_name: str
    batch_size: Decimal
    batch_size_unit: str
    steps: tuple[MasterStep, ...]
    _by_step_id: Mapping[str, MasterStep] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Order steps by step_seq, refuse duplicates and build the step index."""
        steps = tuple(sorted(self.steps, key=_seq_order))
        for attr in ("step_id", "step_seq"):
            seen: set[object] = set()
            for step in steps:
                value = getattr(step, attr)
                if value in seen:
                    raise ValueError(
                        f"master {self.master_record_id} v{self.master_version}: "
                        f"duplicate {attr} {value!r}"
                    )
                seen.add(value)
        object.__setattr__(self, "steps", steps)
        object.__setattr__(
            self, "_by_step_id", MappingProxyType({s.step_id: s for s in steps})
        )

    @classmethod
    def from_rows(cls, rows: Iterable[MasterRow]) -> "MasterVersion":
        """Build one version from its master rows (all from the same version).

        Raises ValueError if there are no rows, or if the rows disagree on any
        header field (id, version, product name, batch size or its unit).
        """
        rows = list(rows)
        if not rows:
            raise ValueError("a master version needs at least one step row")
        header = {name: getattr(rows[0], name) for name in _MASTER_VERSION_FIELDS}
        for row in rows[1:]:
            for name, value in header.items():
                if getattr(row, name) != value:
                    raise ValueError(
                        f"master {header['master_record_id']} "
                        f"v{header['master_version']}: step {row.step_id} has "
                        f"{name} {getattr(row, name)!r}, expected {value!r}"
                    )
        return cls(**header, steps=tuple(MasterStep.from_row(r) for r in rows))

    @property
    def key(self) -> MasterKey:
        """This version's MasterKey."""
        return MasterKey(self.master_record_id, self.master_version)

    def step_key(self, step_id: str) -> StepKey:
        """The StepKey of `step_id` within this version."""
        return StepKey(self.master_record_id, self.master_version, step_id)

    def step(self, step_id: str) -> MasterStep:
        """The step with `step_id` in this version. Raises KeyError if absent."""
        try:
            return self._by_step_id[step_id]
        except KeyError:
            raise KeyError(
                f"master {self.master_record_id} v{self.master_version} "
                f"has no step {step_id}"
            ) from None


def lookup_step(masters: Mapping[MasterKey, MasterVersion], key: StepKey) -> MasterStep:
    """Find a master step by its full StepKey. Raises KeyError if absent."""
    master_key = MasterKey(key.master_record_id, key.master_version)
    try:
        version = masters[master_key]
    except KeyError:
        raise KeyError(
            f"no master {key.master_record_id} v{key.master_version}"
        ) from None
    return version.step(key.step_id)


class BatchStep(BatchRow):
    """One executed batch step: exactly the BatchRow fields, plus its StepKey.

    A blank reading is None (never 0); unit is the raw string as written;
    signatures, timestamps and comment may be None. The comment is plain data
    and nothing here interprets it.
    """

    @classmethod
    def from_row(cls, row: BatchRow) -> "BatchStep":
        """Wrap an already-validated batch row without validating it again."""
        return cls.model_construct(
            **{name: getattr(row, name) for name in BatchRow.model_fields}
        )

    @property
    def step_key(self) -> StepKey:
        """The join key to this step's master step."""
        return StepKey(self.master_record_id, self.master_version, self.step_id)


# BatchStep must stay exactly the approved batch columns.
_check_header(BatchStep, BATCH_HEADER)
