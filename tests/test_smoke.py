"""Placeholder smoke test.

Keeps the test suite non-empty so the pytest hook (.claude/hooks/pytest-run.sh)
has something to run. Replace it once real tests land.
"""

from pharma_batch_record_checker import main


def test_main_prints_greeting(capsys):
    main()
    assert "pharma-batch-record-checker" in capsys.readouterr().out
