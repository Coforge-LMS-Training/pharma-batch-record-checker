"""allow the rule_version 1.1.0 rule ids

Revision ID: 0007_v2_rule_ids
Revises: 0006_review_problem
Create Date: 2026-11-10

"""

from alembic import op

revision = "0007_v2_rule_ids"
down_revision = "0006_review_problem"
branch_labels = None
depends_on = None

_V1_RULE_IDS = (
    "out_of_range",
    "missing_reading",
    "missing_step",
    "missing_performer",
    "missing_second_check",
    "same_person",
    "verifier_before_performer",
    "unit_mismatch",
)
_V2_RULE_IDS = ("late_verification", "performed_out_of_order")


def _replace_rule_check(rule_ids: tuple[str, ...]) -> None:
    """Swap finding_rule_id_known for one allowing exactly these rule ids.

    Drop and add run in one ALTER TABLE, inside the migration's transaction,
    so there is no moment without the check.
    """
    allowed = ", ".join(f"'{rule_id}'" for rule_id in rule_ids)
    op.execute(
        f"""
        ALTER TABLE finding
            DROP CONSTRAINT finding_rule_id_known,
            ADD CONSTRAINT finding_rule_id_known CHECK (rule_id IN ({allowed}))
        """
    )


def upgrade() -> None:
    """Allow the 10 rule ids of rule_version 1.1.0 (adds rules 9 and 10)."""
    _replace_rule_check(_V1_RULE_IDS + _V2_RULE_IDS)


def downgrade() -> None:
    """Restore the 8 rule ids of rule_version 1.0.0.

    Postgres checks the stored rows when the constraint is added, so this
    fails while any late_verification or performed_out_of_order finding is
    stored. That is intended: findings are append-only and are never deleted
    to make a downgrade pass.
    """
    _replace_rule_check(_V1_RULE_IDS)
