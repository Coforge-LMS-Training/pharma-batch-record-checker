"""review problems

Revision ID: 0006_review_problem
Revises: 0005_finding_note
Create Date: 2026-11-09

"""

from alembic import op

revision = "0006_review_problem"
down_revision = "0005_finding_note"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Parse and match problems. A problem row is not a hash-chain event.

    pharma_app receives SELECT and INSERT only. reject_mutation is the same
    function as the other stored tables: a change is allowed only when
    current_user is pharma_admin.
    """
    op.execute(
        """
        CREATE TABLE review_problem (
            problem_id uuid PRIMARY KEY,
            recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            source text NOT NULL,
            file text,
            "row" integer,
            field text,
            reason text NOT NULL,
            raw_value text,
            batch_id text,
            step_id text,
            kind text,
            CONSTRAINT review_problem_source_known CHECK (
                source IN ('parse', 'match')
            ),
            CONSTRAINT review_problem_kind_known CHECK (
                kind IS NULL
                OR kind IN (
                    'unknown_master',
                    'mixed_master',
                    'unknown_step',
                    'duplicate_step',
                    'parse_error'
                )
            ),
            CONSTRAINT review_problem_kind_matches_source CHECK (
                (source = 'parse' AND kind IS NULL)
                OR (source = 'match' AND kind IS NOT NULL)
            )
        )
        """
    )
    op.execute(
        """
        CREATE TRIGGER review_problem_append_only
        BEFORE UPDATE OR DELETE ON review_problem
        FOR EACH ROW
        EXECUTE FUNCTION reject_mutation()
        """
    )
    op.execute("GRANT SELECT, INSERT ON TABLE review_problem TO pharma_app")


def downgrade() -> None:
    """Drop the problem table. The shared reject_mutation function stays."""
    op.execute("DROP TABLE IF EXISTS review_problem")
