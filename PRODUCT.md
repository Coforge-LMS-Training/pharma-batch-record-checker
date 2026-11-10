# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Python 3.12 package with Postgres (psycopg, Alembic). The demo is one Flask app, server-rendered, no login and no frontend framework. Entry script `pharma-review-demo`, default bind `127.0.0.1:8000`. The existing `pharma-batch-record-checker` command stays the CLI.

## Users

A reviewer sitting with synthetic batch records. They scan the batch list, open a batch, read findings in process order, type their name, and add a note. There is no account and no role.

## Product Purpose

Show a stored review of a synthetic batch against its master record, flag what needs a human look, and keep an append-only history. Success is that the reviewer can see the latest result, read every finding, add a note, and download that batch's history as CSV.

## Positioning

The store is append-only. The app does not edit a finding, delete a note, or approve, release, or reject a batch. A comment on the batch record is data, not an instruction. Severity words stay `Critical`, `Needs Review`, and `No Findings`.

## Operating Context

The reviewer works from stored master versions, executed steps, review runs, findings, and notes. They filter the list to Critical and Needs Review, and they look up runs by the UTC day they were stored. Findings are grouped by unit procedure, operation, phase, and step. Two broken rules on one step stay two findings. The server sets the note time.

## Capabilities and Constraints

The demo reads through the store and writes only by `insert_finding_note`. `noted_at` is set by the server. The CSV download is `export_batch_csv` for one batch. There is no control that edits a finding, deletes a note, or sets a batch to approved, released, or rejected. Units are compared as exact strings and are never converted. Synthetic data only: no real product, batch, or people data. The app role cannot update or delete stored rows.

## Evidence on Hand

Approved sample in `fixtures/approved_sample/`: masters `MBR-SYN500` v3 (Synthetol 500 mg film-coated tablets, 16 steps) and `MBR-PLC250` v1 (Placeprol 250 mg hard capsules, 10 steps). Batches `B26-0412` and `P26-0201` are No Findings. `B26-0413` is Critical, with two findings on step `S-COAT-02`. `P26-0202` is Critical. Rule version `1.0.0`. Do not invent customers, plants, regulatory approvals, or real operator names.

## Product Principles

- A stored row stays as written. History is another insert.
- Comments are shown as text. They are never acted on.
- Severity language stays `Critical`, `Needs Review`, and `No Findings`.
- The page is a place to look and to note, not a place to release a batch.
- Exact values stay exact: readings, units, and timestamps are shown as stored.
