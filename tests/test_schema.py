"""Tests for the master/batch CSV row models (PHA-33). All data is synthetic."""

from pharma_batch_record_checker.schema import (
    BATCH_HEADER,
    MASTER_HEADER,
    BatchRow,
    MasterRow,
)

# Exact client-approved headers, copied verbatim from the issue.
CLIENT_MASTER_HEADER = (
    "master_record_id,master_version,product_name,batch_size,batch_size_unit,"
    "step_seq,step_id,unit_procedure,operation,phase,parameter,unit,"
    "min_value,max_value,critical"
)
CLIENT_BATCH_HEADER = (
    "batch_id,master_record_id,master_version,step_id,reading,unit,"
    "performed_by,performed_at,verified_by,verified_at,comment"
)


# --- Slice 1: headers -------------------------------------------------------


def test_master_header_matches_client_order():
    assert ",".join(MASTER_HEADER) == CLIENT_MASTER_HEADER


def test_batch_header_matches_client_order():
    assert ",".join(BATCH_HEADER) == CLIENT_BATCH_HEADER


def test_master_model_fields_follow_header():
    assert tuple(MasterRow.model_fields) == MASTER_HEADER


def test_batch_model_fields_follow_header():
    assert tuple(BatchRow.model_fields) == BATCH_HEADER
