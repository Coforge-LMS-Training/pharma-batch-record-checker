"""Writing a generated dataset as a set of three CSV files (PHA-38 / PHA-40)."""

from pathlib import Path

import pytest

import pharma_batch_record_checker.generator.dataset_io as dataset_io_module
from pharma_batch_record_checker.generator.dataset_io import (
    BATCH_FILE,
    EXPECTED_FILE,
    MASTER_FILE,
    write_dataset,
)
from pharma_batch_record_checker.generator.defects import build_dataset
from pharma_batch_record_checker.generator.expected_findings import (
    EXPECTED_FINDINGS_HEADER,
)
from pharma_batch_record_checker.schema import BATCH_HEADER, MASTER_HEADER

HEADERS = {
    MASTER_FILE: ",".join(MASTER_HEADER),
    BATCH_FILE: ",".join(BATCH_HEADER),
    EXPECTED_FILE: ",".join(EXPECTED_FINDINGS_HEADER),
}


def files_of(out):
    """{file name: bytes} for the three dataset files in out."""
    return {name: (out / name).read_bytes() for name in HEADERS}


def test_write_dataset_writes_three_files_with_exact_headers(tmp_path):
    """The three agreed files are written, each starting with its exact header."""
    write_dataset(build_dataset(0), tmp_path)
    for name, header in HEADERS.items():
        assert (tmp_path / name).read_bytes().split(b"\r\n")[0].decode() == header


def test_same_seed_writes_byte_identical_files(tmp_path):
    """Writing the same seed's dataset twice gives byte-identical files."""
    write_dataset(build_dataset(7), tmp_path / "a")
    write_dataset(build_dataset(7), tmp_path / "b")
    assert files_of(tmp_path / "a") == files_of(tmp_path / "b")


def test_different_seed_changes_batch_bytes(tmp_path):
    """Another seed changes the batch readings, but not the approved masters."""
    write_dataset(build_dataset(7), tmp_path / "a")
    write_dataset(build_dataset(8), tmp_path / "b")
    a, b = files_of(tmp_path / "a"), files_of(tmp_path / "b")
    assert a[BATCH_FILE] != b[BATCH_FILE]
    assert a[MASTER_FILE] == b[MASTER_FILE]


def test_failed_write_keeps_previous_set(tmp_path, monkeypatch):
    """If any file fails to write, the old three files are left as they were."""
    out = tmp_path / "data"
    out.mkdir()
    old = {name: f"old {name}\r\n".encode() for name in HEADERS}
    for name, data in old.items():
        (out / name).write_bytes(data)

    def fail(*args, **kwargs):
        """Stand-in writer that fails after the other files were written."""
        raise OSError("disk full")

    monkeypatch.setattr(dataset_io_module, "write_expected_findings", fail)
    with pytest.raises(OSError, match="disk full"):
        write_dataset(build_dataset(0), out)
    assert files_of(out) == old
    assert sorted(p.name for p in tmp_path.iterdir()) == ["data"]


def test_stages_inside_out_dir(tmp_path, monkeypatch):
    """Staging happens inside --out (same filesystem, no access to its parent needed)."""
    used = []
    real = dataset_io_module.tempfile.mkdtemp

    def spy(*args, **kwargs):
        """Record where the staging folder is created."""
        used.append(Path(kwargs["dir"]))
        return real(*args, **kwargs)

    monkeypatch.setattr(dataset_io_module.tempfile, "mkdtemp", spy)
    out = tmp_path / "data"
    write_dataset(build_dataset(0), out)
    assert used == [out]
    assert sorted(p.name for p in out.iterdir()) == sorted(HEADERS)


def test_failed_move_restores_previous_set(tmp_path, monkeypatch):
    """If moving a new file into place fails, the previous files are restored."""
    out = tmp_path / "data"
    out.mkdir()
    old = {name: f"old {name}\r\n".encode() for name in HEADERS}
    for name, data in old.items():
        (out / name).write_bytes(data)
    real = dataset_io_module.os.replace

    def flaky(src, dst):
        """Fail when the new expected_findings.csv is moved into place."""
        if Path(dst) == out / EXPECTED_FILE and ".old" not in str(src):
            raise PermissionError("locked")
        return real(src, dst)

    monkeypatch.setattr(dataset_io_module.os, "replace", flaky)
    with pytest.raises(PermissionError, match="locked"):
        write_dataset(build_dataset(0), out)
    assert files_of(out) == old
    assert sorted(p.name for p in out.iterdir()) == sorted(HEADERS)
