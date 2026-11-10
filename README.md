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
