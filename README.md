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

- `master_record.csv` holds the synthetic masters: MBR-SYN500 v3 (tablets) and MBR-PLC250 v1 (capsules), plus any catalog products.
- `batch_record.csv` holds the executed batches.
- `expected_findings.csv` is the answer key: one row for every defect the generator planted. The demo does not load it.

Options:

- `--batches N` is the total number of batches, from 1 to 9999 (default 4). Product lines take turns: `B26-0001`, `P26-0001`, `B26-0002`, …
  - A line's batches are clean and defective in turn, starting clean, so its defects start at its 2nd batch.
  - With only the two built-in lines, 3 or more batches plant all 8 rules, because B26-0002 carries the full tablet plan.
- `--start YYYY-MM-DDTHH:MM` is when each line's first batch starts (default `2026-10-12T07:00`).
  - Each later batch on a line starts 3 hours after the previous one could have finished, so times move around the clock and batches can run past midnight.
  - A batch ID takes its year from its start date, and its number restarts at 0001 each year (`B26-…`, then `B27-0001`).
  - The year must be 2000–2099, and a run that would start a batch after 2099 is refused.
- `--seed` changes the readings only, never IDs, times or which batches are defective.
- `--catalog DIR` adds products described as TOML files (process templates, defect plans and products) after B and P. No code is needed to add a product or a new dosage form. See `catalog/README.md`, which comes with an oral solution, a tablet and a capsule.
- `--no-builtin` (with `--catalog`) generates only the catalog's products. The letters B and P become free for them, but the approved masters MBR-SYN500 v3 and MBR-PLC250 v1 can never be reused.

The same options and catalog always give byte-identical files. `data/` is gitignored, so regenerate it instead of committing it.

After writing, the command prints a summary:
- each product line with its batches and defective batches;
- findings by severity and by rule, the time span, and the files;
- if some rules weren't planted, how many `--batches` would plant every rule your plans cover, or that no plan in use has them.

```
uv run generate-batch-records --batches 11 --catalog catalog --out data/
```

`pharma-review-demo --data data/` loads that folder instead of the approved sample, then serves the pages. It stops with a hint if the folder has no `master_record.csv` or `batch_record.csv`. A generated dataset uses the same masters as the approved sample, so if the database already holds the sample (or an earlier dataset), nothing new is loaded and the command says so. Run `docker compose down -v` first to view only the generated data. Plain `uv run pharma-review-demo` still loads the approved sample.
