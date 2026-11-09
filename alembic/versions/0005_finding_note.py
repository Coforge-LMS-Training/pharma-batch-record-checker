"""finding notes

Revision ID: 0005_finding_note
Revises: 0004_append_only
Create Date: 2026-11-09

"""

from alembic import op

revision = "0005_finding_note"
down_revision = "0004_append_only"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Notes on a finding. noted_at is the server clock. Callers do not send it.

    The append-only trigger is the same function as the other stored tables.
    pharma_app receives SELECT and INSERT only.
    """
    op.execute(
        """
        CREATE TABLE finding_note (
            note_id uuid PRIMARY KEY,
            finding_id uuid NOT NULL REFERENCES finding (finding_id),
            reviewer_name text NOT NULL,
            note_text text NOT NULL,
            noted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            CONSTRAINT finding_note_reviewer_present CHECK (btrim(reviewer_name) <> ''),
            CONSTRAINT finding_note_text_present CHECK (btrim(note_text) <> '')
        )
        """
    )
    op.execute(
        """
        CREATE INDEX finding_note_finding_noted_idx
        ON finding_note (finding_id, noted_at)
        """
    )
    op.execute(
        """
        CREATE TRIGGER finding_note_append_only
        BEFORE UPDATE OR DELETE ON finding_note
        FOR EACH ROW
        EXECUTE FUNCTION reject_mutation()
        """
    )
    op.execute("GRANT SELECT, INSERT ON TABLE finding_note TO pharma_app")


def downgrade() -> None:
    """Drop the note table. The shared reject_mutation function stays."""
    op.execute("DROP TABLE IF EXISTS finding_note")
