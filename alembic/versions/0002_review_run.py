"""review runs and findings

Revision ID: 0002_review_run
Revises: 0001_master_batch
Create Date: 2026-11-09

"""

from alembic import op

revision = "0002_review_run"
down_revision = "0001_master_batch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Review runs, one row per finding, and grants for the app role."""
    op.execute(
        """
        CREATE TABLE review_run (
            run_id uuid PRIMARY KEY,
            batch_id text NOT NULL REFERENCES batch (batch_id),
            rule_version text NOT NULL,
            result_category text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            CONSTRAINT review_run_result_category_known CHECK (
                result_category IN ('Critical', 'Needs Review', 'No Findings')
            )
        )
        """
    )
    op.execute(
        """
        CREATE INDEX review_run_batch_created_idx
        ON review_run (batch_id, created_at)
        """
    )
    op.execute(
        """
        CREATE TABLE finding (
            finding_id uuid PRIMARY KEY,
            run_id uuid NOT NULL REFERENCES review_run (run_id),
            finding_seq integer NOT NULL,
            rule_id text NOT NULL,
            step_severity text NOT NULL,
            detail text NOT NULL,
            batch_id text NOT NULL,
            master_record_id text NOT NULL,
            master_version text NOT NULL,
            step_id text NOT NULL,
            step_seq integer NOT NULL,
            unit_procedure text NOT NULL,
            operation text NOT NULL,
            phase text NOT NULL,
            parameter text,
            critical boolean NOT NULL,
            expected_unit text,
            min_value numeric,
            max_value numeric,
            reading numeric,
            recorded_unit text,
            performed_by text,
            performed_at timestamp without time zone,
            verified_by text,
            verified_at timestamp without time zone,
            comment text,
            CONSTRAINT finding_run_seq_unique UNIQUE (run_id, finding_seq),
            CONSTRAINT finding_seq_nonnegative CHECK (finding_seq >= 0),
            CONSTRAINT finding_step_seq_positive CHECK (step_seq >= 1),
            CONSTRAINT finding_rule_id_known CHECK (
                rule_id IN (
                    'out_of_range',
                    'missing_reading',
                    'missing_step',
                    'missing_performer',
                    'missing_second_check',
                    'same_person',
                    'verifier_before_performer',
                    'unit_mismatch'
                )
            ),
            CONSTRAINT finding_step_severity_known CHECK (
                step_severity IN ('Critical', 'Needs Review')
            )
        )
        """
    )
    op.execute("GRANT SELECT, INSERT ON TABLE review_run, finding TO pharma_app")


def downgrade() -> None:
    """Drop the review tables. Earlier tables stay."""
    op.execute("DROP TABLE IF EXISTS finding")
    op.execute("DROP TABLE IF EXISTS review_run")
