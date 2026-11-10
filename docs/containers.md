# Container architecture

How the review app runs in Docker: what each container does, how they start, where data lives, and what to keep in mind. The commands are in the README's **Run in Docker** section.

## At a glance

Two separate jobs share one volume:

```
 Fill the data (when you choose)            Boot the app (when you choose)
 docker compose run --rm generator          docker compose --profile app up --build -d

 ┌──────────────────────────┐               ┌──────────────────────────┐
 │ generator                │               │ postgres (postgres:16)   │
 │ (pharma-review:local)    │               │ healthcheck: pg_isready  │
 │ generate-batch-records   │               │ volume: pharma_postgres  │
 │   --batches $BATCHES     │               └────────────┬─────────────┘
 │   --seed $SEED           │                            │ healthy
 │   --catalog catalog      │                            ▼
 │   --out /data            │               ┌──────────────────────────┐
 │ runs once, exits         │               │ app (pharma-review:local)│
 └────────────┬─────────────┘               │ pharma-review-demo       │
              │ writes (rw)                 │   --host 0.0.0.0         │
              ▼                             │   --data /data           │
     ┌──────────────────┐   reads (ro)      │ migrate → load → serve   │
     │ volume           │◀──────────────────│                          │
     │ pharma_data      │                   └────────────┬─────────────┘
     │ (external)       │                                │
     └──────────────────┘              127.0.0.1:${APP_PORT:-8000} (this machine only)
```

Booting the app never runs the generator, and running the generator never touches the database.

## Services

| Service | Image | Profile | Started by | Volumes | Ports |
|---|---|---|---|---|---|
| `postgres` | `postgres:16` | none | `--profile app up`, plain `up`, the host demo | `pharma_postgres` → `/var/lib/postgresql/data` | `${POSTGRES_PORT:-5432}:5432` |
| `generator` | `pharma-review:local` (this repo's `Dockerfile`) | `tools` | `docker compose run --rm generator` only | `pharma_data` → `/data` (read-write) | none |
| `app` | `pharma-review:local` | `app` | `docker compose --profile app up` | `pharma_data` → `/data` (read-only) | `127.0.0.1:${APP_PORT:-8000}:8000` |

- `generator` and `app` use the same image. It holds both commands, the migrations, the approved sample and the generator's catalog.
- `docker compose run generator` turns on the `tools` profile by itself, so you never type `--profile tools`.

## Filling the data

`docker compose run --rm generator` (or with `BATCHES`/`SEED`, or with any `generate-batch-records` flags):
- writes `master_record.csv`, `batch_record.csv` and `expected_findings.csv` into `pharma_data`, replacing the previous three files as a set;
- prints its summary and exits; `--rm` removes the container.

It doesn't need Postgres and doesn't start it. Passing flags replaces the default command, so include `--out /data`.

## Booting the app

`docker compose --profile app up --build -d`:
1. **`postgres`** starts. Compose waits until `pg_isready` passes.
2. **`app`** starts:
   - applies the Alembic migrations as `pharma_admin`;
   - loads `/data` as `pharma_app` (parse, match, the 8 rules, store, hash chain);
   - serves the pages. Its healthcheck GETs `/`, and `up --wait` returns once it's healthy.
3. **If `pharma_data` holds no dataset,** the app exits before loading with a message to run `docker compose run --rm generator`. There's no fallback to the approved sample.

## Data

| Volume | Docker name | Holds | Written by | Removed by |
|---|---|---|---|---|
| `pharma_postgres` | `pharma-batch-record-checker_pharma_postgres` | the database (masters, batches, runs, findings, notes, audit chain) | the app | `docker compose --profile app down -v` |
| `pharma_data` | **`pharma_data`** | the generated CSVs | the generator only | only `docker volume rm pharma_data` |

- **`pharma_data` is external.** You create it once with `docker volume create pharma_data`. Compose never creates or deletes it, so `down -v` resets the database and keeps your generated data.
- **The database is append-only.** If it already holds a master or batch from `/data`, the load stores nothing new, says so, and the app serves what's stored. To show a refilled `pharma_data`, reset the database with `down -v` and boot again.
- **The host's `./data` folder isn't used by the containers.** It belongs to the host (`uv`) workflow.

## Configuration

| Variable | Default | Used by | Meaning |
|---|---|---|---|
| `BATCHES` | `11` | `docker compose run generator` | `--batches` (only when no flags are passed) |
| `SEED` | `0` | `docker compose run generator` | `--seed` (only when no flags are passed) |
| `APP_PORT` | `8000` | app | host port, bound to 127.0.0.1 |
| `POSTGRES_PORT` | `5432` | postgres | host port |
| `DATABASE_URL` | `postgresql://pharma_app:pharma_app@postgres:5432/pharma` | app | the app role: SELECT and INSERT only |
| `DATABASE_ADMIN_URL` | `postgresql://pharma_admin:pharma_admin@postgres:5432/pharma` | app | migrations |
| `PHARMA_ROOT` | `/app` | app | where `alembic.ini` and `fixtures/approved_sample/` are. The image has no `docker-compose.yml` for the app to find its root by. |

Inside the network the database host is `postgres`, not `localhost`, so the app never tries to start Compose itself.

## Image

`Dockerfile`, multi-stage:
- **build:** `python:3.12-slim-bookworm` plus `uv` 0.12.23. Runs `uv sync --locked --no-dev`: dependencies first (a cached layer), then the package, not editable.
- **runtime:** `python:3.12-slim-bookworm` with `/app/.venv`, `alembic.ini`, `alembic/`, `fixtures/approved_sample/` and `catalog/`.
  - It runs as the non-root user **`app`** (uid 1000).
  - `/data` is created and owned by `app`. A new, empty `pharma_data` takes that ownership the first time it's mounted, so the generator can write without any extra flags.

`.dockerignore` keeps these out of the build context, and so out of the image:
- `sample/` (client sample CSVs);
- `data/`;
- `.env`;
- `CLAUDE.local.md`, `.claude/`;
- `.git`, `.venv`, caches, `tests/`, `docs/`.

`tests/test_container_files.py` checks these rules without Docker:
- no `sample/`, and a non-root user;
- the app on 127.0.0.1;
- the separate profiles (`app`, `tools`), and the app not depending on the generator;
- `pharma_data` external and named, mounted read-only by the app.

## Living with the host workflow

`uv run pharma-review-demo`, on the host, runs a plain `docker compose up -d --wait` when Postgres isn't running. That starts **only `postgres`**, and it works even if `pharma_data` doesn't exist.

The two workflows share `postgres` and its database, but not the port 8000 server, so run one or the other:
- stop the app container with `docker compose --profile app down` before running the host demo;
- or give the container another port with `APP_PORT`.

Running the host demo against a database that already holds a generated dataset prints "Approved sample is already loaded.", because the masters are shared. To see the sample there, run `down -v` first.

## Security notes (local demo only)

- **Localhost only.** The app's port is published on `127.0.0.1`. Note-posting has no CSRF protection, and the pages have no login.
- **Development server.** It runs Flask's built-in server, not a production server.
- **Demo credentials.** They're plain values in `docker-compose.yml` and `.env.example`, and they're not secrets.
- **Admin credentials in the app.** The `app` container holds the admin URL because it runs the migrations itself.
- **Read-only data for the app.** The app can't change the generated files: `/data` is mounted read-only, and only the generator writes it.

## Next steps (not done here)

- Serve with gunicorn instead of the Flask development server.
- A separate one-shot `migrate` service, so `app` only holds the `pharma_app` credentials.
- Build the image in CI (`docker compose --profile app build`) and run the database tests against the Compose Postgres. Today those 45 tests are skipped in CI.
- An approved-sample mode in Docker. Today the sample is loaded by the host workflow.
- Move `pytest` and `ruff` to dev dependencies, so the `--no-dev` image doesn't carry them.
