# pharma-batch-record-checker

## Store and review demo

From a fresh clone:

```
uv sync
uv run pharma-review-demo
```

That command starts the local Postgres when it is not already running, applies the migrations, loads the approved sample when it is not already stored, and serves http://127.0.0.1:8000. The same command is enough the next time. Docker is required for that local database.

The pages list stored batches, open one batch's findings, append a note, and download that batch's CSV. `pharma-batch-record-checker` stays the CLI. The store writes through `insert_master`, `insert_batch`, `insert_review_run`, and `insert_finding_note`.

Set `DATABASE_URL` and `DATABASE_ADMIN_URL` first when you are not using the local database. Those URLs are in `.env.example`. `uv run pytest` runs the tests. Database tests skip when `DATABASE_URL` is unset, and they clear the stored rows, so run `uv run pharma-review-demo` again afterward to load the sample.

## Generate a dataset and view it

The generator writes synthetic batch records to a folder on disk. No database is needed to generate them.

```
uv run generate-batch-records --seed 0 --batches 10 --out data/
docker compose down -v                 # optional: start from an empty local database
uv run pharma-review-demo --data data/
```

`generate-batch-records` writes three files to `--out`:

- `master_record.csv` holds the two synthetic masters, MBR-SYN500 v3 (tablets) and MBR-PLC250 v1 (capsules).
- `batch_record.csv` holds the executed batches.
- `expected_findings.csv` is the answer key: one row for every defect the generator planted. The demo does not load it.

`--batches` is the total number of batches, from 1 to 162. Batches alternate between the two products (`B26-0001`, `P26-0001`, `B26-0002`, …), and every second batch of each product has planted defects, so 3 or more batches cover all 8 rules. The same `--seed` and `--batches` always give byte-identical files. `data/` is gitignored, so regenerate it instead of committing it.

`pharma-review-demo --data data/` loads that folder instead of the approved sample, then serves the pages. It stops with a hint if the folder has no `master_record.csv` or `batch_record.csv`. A generated dataset uses the same masters as the approved sample, so if the database already holds the sample (or an earlier dataset), nothing new is loaded and the command says so. Run `docker compose down -v` first to view only the generated data. Plain `uv run pharma-review-demo` still loads the approved sample.
