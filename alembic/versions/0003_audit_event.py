"""audit event chain

Revision ID: 0003_audit_event
Revises: 0002_review_run
Create Date: 2026-11-09

"""

from alembic import op

revision = "0003_audit_event"
down_revision = "0002_review_run"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Event rows, and admin-owned functions that advance the existing head.

    pharma_app does not receive UPDATE on audit_head. reserve_audit_slot and
    append_audit_event run as pharma_admin, so the head still moves inside the
    app transaction. The app role can read events. It cannot insert them.
    """
    op.execute(
        """
        CREATE TABLE audit_event (
            event_seq bigint PRIMARY KEY,
            event_type text NOT NULL,
            entity_id text NOT NULL,
            payload_hash text NOT NULL,
            prev_event_hash text NOT NULL,
            event_hash text NOT NULL,
            created_at timestamptz NOT NULL,
            CONSTRAINT audit_event_type_known CHECK (
                event_type IN (
                    'master_import',
                    'batch_import',
                    'review_run',
                    'finding_note'
                )
            ),
            CONSTRAINT audit_event_hash_hex CHECK (
                payload_hash ~ '^[0-9a-f]{64}$'
                AND prev_event_hash ~ '^[0-9a-f]{64}$'
                AND event_hash ~ '^[0-9a-f]{64}$'
            )
        )
        """
    )
    op.execute(
        """
        CREATE FUNCTION reserve_audit_slot()
        RETURNS TABLE (
            event_seq bigint,
            prev_event_hash text,
            created_at timestamptz
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public
        AS $$
        DECLARE
            seq bigint;
            prev text;
        BEGIN
            SELECT last_event_seq, last_event_hash
              INTO seq, prev
              FROM audit_head
             WHERE id = 1
             FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'audit_head is missing';
            END IF;
            event_seq := seq + 1;
            prev_event_hash := prev;
            created_at := clock_timestamp();
            RETURN NEXT;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION append_audit_event(
            p_event_seq bigint,
            p_event_type text,
            p_entity_id text,
            p_payload_hash text,
            p_prev_event_hash text,
            p_event_hash text,
            p_created_at timestamptz
        )
        RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public
        AS $$
        DECLARE
            head_seq bigint;
            head_hash text;
        BEGIN
            SELECT last_event_seq, last_event_hash
              INTO head_seq, head_hash
              FROM audit_head
             WHERE id = 1
             FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'audit_head is missing';
            END IF;
            IF head_seq + 1 <> p_event_seq OR head_hash <> p_prev_event_hash THEN
                RAISE EXCEPTION 'audit_head does not match the reserved event';
            END IF;
            INSERT INTO audit_event (
                event_seq, event_type, entity_id, payload_hash,
                prev_event_hash, event_hash, created_at
            ) VALUES (
                p_event_seq, p_event_type, p_entity_id, p_payload_hash,
                p_prev_event_hash, p_event_hash, p_created_at
            );
            UPDATE audit_head
               SET last_event_seq = p_event_seq,
                   last_event_hash = p_event_hash
             WHERE id = 1;
        END;
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION reserve_audit_slot() FROM PUBLIC")
    op.execute(
        """
        REVOKE ALL ON FUNCTION append_audit_event(
            bigint, text, text, text, text, text, timestamptz
        ) FROM PUBLIC
        """
    )
    op.execute("GRANT EXECUTE ON FUNCTION reserve_audit_slot() TO pharma_app")
    op.execute(
        """
        GRANT EXECUTE ON FUNCTION append_audit_event(
            bigint, text, text, text, text, text, timestamptz
        ) TO pharma_app
        """
    )
    op.execute("GRANT SELECT ON TABLE audit_event TO pharma_app")


def downgrade() -> None:
    """Drop the event table and the functions that write it. The head row stays."""
    op.execute(
        """
        DROP FUNCTION IF EXISTS append_audit_event(
            bigint, text, text, text, text, text, timestamptz
        )
        """
    )
    op.execute("DROP FUNCTION IF EXISTS reserve_audit_slot()")
    op.execute("DROP TABLE IF EXISTS audit_event")
