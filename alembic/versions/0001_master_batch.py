"""master and batch tables

Revision ID: 0001_master_batch
Revises:
Create Date: 2026-11-09

"""

from alembic import op

revision = "0001_master_batch"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Master and batch tables, the app role, and the empty chain head."""
    op.execute(
        """
        CREATE TABLE master_record (
            master_record_id text NOT NULL,
            master_version text NOT NULL,
            product_name text NOT NULL,
            batch_size numeric NOT NULL,
            batch_size_unit text NOT NULL,
            PRIMARY KEY (master_record_id, master_version),
            CONSTRAINT master_record_batch_size_positive CHECK (batch_size > 0)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE master_step (
            master_record_id text NOT NULL,
            master_version text NOT NULL,
            step_seq integer NOT NULL,
            step_id text NOT NULL,
            unit_procedure text NOT NULL,
            operation text NOT NULL,
            phase text NOT NULL,
            parameter text,
            unit text,
            min_value numeric,
            max_value numeric,
            critical boolean NOT NULL,
            PRIMARY KEY (master_record_id, master_version, step_id),
            CONSTRAINT master_step_master_fk
                FOREIGN KEY (master_record_id, master_version)
                REFERENCES master_record (master_record_id, master_version),
            CONSTRAINT master_step_seq_unique
                UNIQUE (master_record_id, master_version, step_seq),
            CONSTRAINT master_step_seq_positive CHECK (step_seq >= 1)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE batch (
            batch_id text PRIMARY KEY,
            master_record_id text NOT NULL,
            master_version text NOT NULL,
            CONSTRAINT batch_master_fk
                FOREIGN KEY (master_record_id, master_version)
                REFERENCES master_record (master_record_id, master_version)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE batch_step (
            batch_id text NOT NULL,
            step_id text NOT NULL,
            reading numeric,
            unit text,
            performed_by text,
            performed_at timestamp without time zone,
            verified_by text,
            verified_at timestamp without time zone,
            comment text,
            PRIMARY KEY (batch_id, step_id),
            CONSTRAINT batch_step_batch_fk
                FOREIGN KEY (batch_id) REFERENCES batch (batch_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE audit_head (
            id integer PRIMARY KEY,
            last_event_seq bigint NOT NULL,
            last_event_hash text NOT NULL,
            CONSTRAINT audit_head_singleton CHECK (id = 1)
        )
        """
    )
    op.execute(
        """
        INSERT INTO audit_head (id, last_event_seq, last_event_hash)
        VALUES (1, 0, repeat('0', 64))
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pharma_app') THEN
                CREATE ROLE pharma_app LOGIN PASSWORD 'pharma_app';
            END IF;
        END
        $$
        """
    )
    op.execute("GRANT CONNECT ON DATABASE pharma TO pharma_app")
    op.execute("GRANT USAGE ON SCHEMA public TO pharma_app")
    op.execute(
        """
        GRANT SELECT, INSERT ON TABLE
            master_record,
            master_step,
            batch,
            batch_step,
            audit_head
        TO pharma_app
        """
    )


def downgrade() -> None:
    """Drop the tables. The pharma_app role is left in place."""
    op.execute("DROP TABLE IF EXISTS batch_step")
    op.execute("DROP TABLE IF EXISTS batch")
    op.execute("DROP TABLE IF EXISTS master_step")
    op.execute("DROP TABLE IF EXISTS master_record")
    op.execute("DROP TABLE IF EXISTS audit_head")
