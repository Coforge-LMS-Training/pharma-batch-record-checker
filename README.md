# pharma-batch-record-checker

## Store and review demo

Start Postgres, apply the migrations, and run the tests. Database tests skip when `DATABASE_URL` is unset. The URLs are in `.env.example`.

```
docker compose up -d
uv run alembic upgrade head
uv run pytest
uv run pharma-review-demo
```

`pharma-review-demo` listens on `127.0.0.1:8000`. It lists stored batches, opens one batch's findings, appends a note, and downloads that batch's CSV. `pharma-batch-record-checker` stays the CLI.

The store writes through `insert_master`, `insert_batch`, `insert_review_run`, and `insert_finding_note`. Load the approved sample with `load_and_review` before opening the demo:

```
uv run python -c "from pharma_batch_record_checker.store.load import load_and_review; from pharma_batch_record_checker.store.review_store import PostgresReviewStore; load_and_review('fixtures/approved_sample/master_record.csv', 'fixtures/approved_sample/batch_record.csv', PostgresReviewStore())"
```