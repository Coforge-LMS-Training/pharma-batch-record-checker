"""Migration 0007 widens the finding rule-id check to the v2 rule ids.

It ships ahead of the engine, so every branch knows the revision and a
database migrated on the v2 branch still boots on main. The static tests need
no database; the last one runs only when DATABASE_URL is set.
"""

import importlib.util
import os
from pathlib import Path
from types import ModuleType

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from pharma_batch_record_checker.rules import RULE_IDS

ROOT = Path(__file__).resolve().parents[2]
REVISION = "0007_v2_rule_ids"
V2_RULE_IDS = ("late_verification", "performed_out_of_order")


def script_directory() -> ScriptDirectory:
    """The repository's Alembic scripts, as the app's migrate step sees them."""
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    return ScriptDirectory.from_config(cfg)


def load_migration() -> ModuleType:
    """Import the 0007 migration file as a module."""
    path = ROOT / "alembic" / "versions" / f"{REVISION}.py"
    spec = importlib.util.spec_from_file_location(REVISION, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0007_is_the_single_head_after_0006():
    """0007 is the only head and follows 0006, so upgrade head reaches it."""
    scripts = script_directory()
    assert scripts.get_heads() == [REVISION]
    assert scripts.get_revision(REVISION).down_revision == "0006_review_problem"


def test_0007_allows_every_engine_rule_id_and_the_v2_ones():
    """0007 allows the 8 v1 ids plus rules 9 and 10, and every id the engine has.

    Holds before and after the engine turns the v2 rules on.
    """
    migration = load_migration()
    allowed = migration._V1_RULE_IDS + migration._V2_RULE_IDS
    assert len(migration._V1_RULE_IDS) == 8
    assert migration._V2_RULE_IDS == V2_RULE_IDS
    assert len(set(allowed)) == 10
    assert set(RULE_IDS) <= set(allowed)


@pytest.mark.db
@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL is unset")
def test_finding_check_allows_all_ten_rule_ids(admin):
    """After upgrade head, finding_rule_id_known names every v1 and v2 rule id."""
    row = admin.execute(
        "SELECT pg_get_constraintdef(oid) AS definition FROM pg_constraint "
        "WHERE conname = 'finding_rule_id_known'"
    ).fetchone()
    for rule_id in set(RULE_IDS) | set(V2_RULE_IDS):
        assert f"'{rule_id}'" in row["definition"]
