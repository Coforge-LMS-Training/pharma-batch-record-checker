"""Errors raised by the store.

These are load and review failures. They are not rule findings, and a raised
error means the transaction stored nothing.
"""


class StoreError(Exception):
    """A store write was refused and left the database unchanged."""


class DuplicateRecord(StoreError):
    """That master version, batch, or problem set is already stored."""


class MasterNotFound(StoreError):
    """The batch names a master version that is not stored."""


class BatchNotFound(StoreError):
    """The review names a batch that is not stored."""


class InvalidReview(StoreError):
    """The review run was rejected and nothing was stored."""


class FindingNotFound(StoreError):
    """The note names a finding that is not stored."""


class StaleReview(StoreError):
    """The batch's latest run is not the one the re-review started from."""
