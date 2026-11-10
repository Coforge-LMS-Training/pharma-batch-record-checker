# Container architecture

How the review app runs in Docker: what each container does, how they start, where data lives, and what to keep in mind. The commands are in the README's **Run in Docker** section.

## At a glance

```
docker compose --profile app up --build -d

 ┌──────────────────────────── Compose network (default) ─────────────────────────────┐
 │                                                                                     │
 │  postgres  (postgres:16)          generator  (pharma-review:local)                 │
 │  ─ healthcheck: pg_isready        ─ generate-batch-records                          │
 │  ─ volume: pharma_postgres          --batches $BATCHES --seed $SEED                 │
 │      │                               --catalog catalog --out /data                  │
 │      │                            ─ volume: pharma_data → /data (read-write)        │
 │      │                            ─ runs once, exits 0                              │
 │      │                                         │                                    │
 │      │        app  (pharma-review:local)       ▼                                    │
 │      └──────▶ ─ pharma-review-demo --host 0.0.0.0 --data /data                      │
 │               ─ migrate → load /data → serve                                        │
 │               ─ volume: pharma_data → /data (read-only)                             │
 │               ─ port 127.0.0.1:${APP_PORT:-8000} → 8000                             │
 └─────────────────────────────────────────────────────────────────────────────────────┘
                                   │
                    http://127.0.0.1:8000  (this machine only)
```

## Services

| Service | Image | Profile | Runs | Volumes | Ports |
|---|---|---|---|---|---|
| `postgres` | `postgres:16` | none (always) | the database | `pharma_postgres` → `/var/lib/postgresql/data` | `${POSTGRES_PORT:-5432}:5432` |
| `generator` | `pharma-review:local` (this repo's `Dockerfile`) | `app` | `generate-batch-records … --out /data`, once | `pharma_data` → `/data` (read-write) | none |
| `app` | `pharma-review:local` | `app` | `pharma-review-demo --host 0.0.0.0 --data /data` | `pharma_data` → `/data` (read-only) | `127.0.0.1:${APP_PORT:-8000}:8000` |

`generator` and `app` are built from the same image. The image holds both commands, the migrations, the approved sample and the generator's catalog.

## Boot sequence

1. **`postgres`** starts. Compose waits until its healthcheck (`pg_isready`) passes.
2. **`generator`** starts once Postgres is healthy. It writes `master_record.csv`, `batch_record.csv` and `expected_findings.csv` into `pharma_data`, replacing any previous files there as a set, prints its summary and exits.
3. **`app`** starts only after the generator **exits with code 0** (`service_completed_successfully`). If generation fails, the app doesn't start, and `docker compose logs generator` says why. The app then:
   - applies the Alembic migrations as `pharma_admin`;
   - loads `/data` as `pharma_app`: parse, match, the 8 rules, store, hash chain;
   - serves the pages. Its healthcheck GETs `/`.

`up --wait` returns once `app` is healthy.

## Data

| Volume | Docker name | Holds | Written by | Removed by |
|---|---|---|---|---|
| `pharma_postgres` | `pharma-batch-record-checker_pharma_postgres` | the database (masters, batches, runs, findings, notes, audit chain) | the app | `docker compose --profile app down -v` |
| `pharma_data` | **`pharma_data`** (fixed, no prefix) | the generated CSVs | the generator, on every `up` | `docker compose --profile app down -v`, or `docker volume rm pharma_data` |

- **`pharma_data` is rewritten on every `up`.** The generator runs each time. With the same `BATCHES`/`SEED`, the files come out identical.
- **The database is append-only.** If it already holds a master or batch from `/data`, the load stores nothing new and says so, and the app still serves what's stored. For a different dataset, run `down -v` first, which empties both volumes.
- **The host's `./data` folder isn't used by the containers.** It belongs to the host (`uv`) workflow.

## Configuration

| Variable | Default | Used by | Meaning |
|---|---|---|---|
| `BATCHES` | `11` | generator | `--batches` |
| `SEED` | `0` | generator | `--seed` |
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
  - `/data` is created and owned by `app`. A fresh `pharma_data` volume takes that ownership, which is why the generator can write without any extra flags.

`.dockerignore` keeps these out of the build context, and so out of the image:
- `sample/` (client sample CSVs);
- `data/`;
- `.env`;
- `CLAUDE.local.md`, `.claude/`;
- `.git`, `.venv`, caches, `tests/`, `docs/`.

`tests/test_container_files.py` checks these rules without Docker:
- no `sample/`, and a non-root user;
- the app on 127.0.0.1;
- the profiles;
- the volume name and the read-only mount;
- the app waiting for the generator.

## Living with the host workflow

`uv run pharma-review-demo`, on the host, runs a plain `docker compose up -d --wait` when Postgres isn't running. `generator` and `app` are behind the **`app` profile**, so that starts **only `postgres`**.

The two workflows share the `postgres` service and the `pharma_postgres` database, but not the port 8000 server, so run one or the other:
- stop the containers with `docker compose --profile app down` before running the host demo;
- or give the container another port with `APP_PORT`.

Running the host demo against a database that already holds a generated dataset prints "Approved sample is already loaded.", because the masters are shared. To see the sample there, run `down -v` first.

## Security notes (local demo only)

- **Localhost only.** The app's port is published on `127.0.0.1`. Note-posting has no CSRF protection, and the pages have no login.
- **Development server.** It runs Flask's built-in server, not a production server.
- **Demo credentials.** They're plain values in `docker-compose.yml` and `.env.example`, and they're not secrets.
- **Admin credentials in the app.** The `app` container holds the admin URL because it runs the migrations itself.
- **Read-only data.** The app can't change the generated files, because `/data` is mounted read-only.

## Next steps (not done here)

- Serve with gunicorn instead of the Flask development server.
- A separate one-shot `migrate` service, so `app` only holds the `pharma_app` credentials.
- Build the image in CI (`docker compose --profile app build`) and run the database tests against the Compose Postgres. Today those 45 tests are skipped in CI.
- An approved-sample mode in Docker. Today the sample is loaded by the host workflow.
- Move `pytest` and `ruff` to dev dependencies, so the `--no-dev` image doesn't carry them.
