# pharma-batch-record-checker

## Run in Docker

Filling the data and booting the app are **separate**. The `generator` container writes a dataset into the Docker volume **`pharma_data`**. The `app` container, with Postgres, loads whatever `pharma_data` holds and serves the pages. You only need Docker; `uv` isn't needed on the host.

```bash
docker volume create pharma_data                 # once per machine
docker compose run --rm generator                # fill pharma_data (11 batches, seed 0, with the catalog)
docker compose --profile app up --build -d       # boot Postgres + app → http://127.0.0.1:8000
```

| Task | Command |
|---|---|
| Fill or refill `pharma_data` with other sizes or seeds | `BATCHES=50 SEED=7 docker compose run --rm generator` |
| Fill `pharma_data` with any generator flags | `docker compose run --rm generator --batches 6 --catalog catalog --no-builtin --out /data` |
| List the files in `pharma_data` | `docker compose run --rm --entrypoint ls generator -l /data` |
| Boot the app | `docker compose --profile app up --build -d` |
| Follow the app's log | `docker compose --profile app logs -f app` |
| Show a new dataset | refill `pharma_data`, then `docker compose --profile app down -v`, then `docker compose --profile app up -d` |
| Stop, keeping the database | `docker compose --profile app down` |
| Reset the database only (`pharma_data` is kept) | `docker compose --profile app down -v` |
| Delete the generated data | `docker volume rm pharma_data` (then `docker volume create pharma_data` before the next fill) |
| Use another port | `APP_PORT=8080 docker compose --profile app up -d` |

- **Booting the app never runs the generator,** and refilling `pharma_data` never touches the database.
- **The database is append-only and keeps what it first loaded.** After refilling `pharma_data`, reset it with `down -v` so the app loads the new dataset.
- **The app is published on `127.0.0.1` only.**

| Problem | Fix |
|---|---|
| `external volume "pharma_data" not found` | Run `docker volume create pharma_data` once. |
| The app exits: `/data has no master_record.csv or batch_record.csv` | `pharma_data` is empty. Run `docker compose run --rm generator`, then `up` again. |
| The page shows an older dataset, or the log says "Nothing new loaded" | The database already holds those masters or batches. Run `down -v`, then `up`. |
| `port is already allocated` | The host demo (`uv run pharma-review-demo`) or something else is on 8000. Stop it, or set `APP_PORT=8080`. |
| Plain `docker compose up` starts only Postgres | That's intended. The app needs `--profile app`, and the generator runs with `docker compose run`. |

Architecture, volumes, environment and security notes: [`docs/containers.md`](docs/containers.md).

## Store and review demo

From a fresh clone:

```
uv sync
uv run pharma-review-demo
```

That command starts the local Postgres when it is not already running, applies the migrations, loads the approved sample when it is not already stored, and serves http://127.0.0.1:8000. The same command is enough the next time. Docker is required for that local database.

The pages are an Overview, a searchable Batches list with a calendar and status tabs, one page per batch (its findings, notes, and CSV download or email link), and a Guide with a glossary and FAQ for readers new to batch records. `pharma-batch-record-checker` stays the CLI. The store writes through `insert_master`, `insert_batch`, `insert_review_run`, and `insert_finding_note`.

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

Common options:
- `--batches N` (1–9999, default 4);
- `--seed S`, which changes readings only;
- `--start YYYY-MM-DDTHH:MM`;
- `--catalog DIR`, which adds TOML-described products;
- `--no-builtin`, which with `--catalog` generates only the catalog's products.

The same options always give byte-identical files. `data/` is gitignored, so regenerate it instead of committing it.

**Full usage** covers every flag, how batches and defects are planned, how many batches you need, the output files, the summary and exit codes. It's in [`src/pharma_batch_record_checker/generator/README.md`](src/pharma_batch_record_checker/generator/README.md). For catalog products, see [`catalog/README.md`](catalog/README.md).

`pharma-review-demo --data data/` loads that folder instead of the approved sample, then serves the pages. It stops with a hint if the folder has no `master_record.csv` or `batch_record.csv`. A generated dataset uses the same masters as the approved sample, so if the database already holds the sample (or an earlier dataset), nothing new is loaded and the command says so. Run `docker compose down -v` first to view only the generated data. Plain `uv run pharma-review-demo` still loads the approved sample.
