"""append-only triggers

Revision ID: 0004_append_only
Revises: 0003_audit_event
Create Date: 2026-11-09

"""

from alembic import op

revision = "0004_append_only"
down_revision = "0003_audit_event"
branch_labels = None
depends_on = None

_TABLES = (
    "master_record",
    "master_step",
    "batch",
    "batch_step",
    "review_run",
    "finding",
    "audit_event",
    "audit_head",
)


def upgrade() -> None:
    """Reject UPDATE and DELETE unless the session is pharma_admin.

    current_user is the check, not session_user. append_audit_event is
    SECURITY DEFINER and owned by pharma_admin, so its update of audit_head
    still runs. A direct update by pharma_app does not, even if UPDATE is
    granted later. pharma_app keeps SELECT and INSERT. There is no truncate
    trigger: the test fixture clears tables as pharma_admin.
    """
    op.execute(
        """
        CREATE FUNCTION reject_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF current_user = 'pharma_admin' THEN
                IF TG_OP = 'DELETE' THEN
                    RETURN OLD;
                END IF;
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'append-only: % on %.% is not allowed',
                TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME;
        END;
        $$
        """
    )
    for table in _TABLES:
        op.execute(
            f"""
            CREATE TRIGGER {table}_append_only
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW
            EXECUTE FUNCTION reject_mutation()
            """
        )
    op.execute("REVOKE ALL ON FUNCTION reject_mutation() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION reject_mutation() TO pharma_app")


def downgrade() -> None:
    """Drop the triggers and the function. Grants on the tables stay as they were."""
    for table in _TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
    op.execute("DROP FUNCTION IF EXISTS reject_mutation()")
