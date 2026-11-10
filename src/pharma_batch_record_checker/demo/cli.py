"""Run the review demo.

The default bind is 127.0.0.1:8000. This command is separate from
pharma-batch-record-checker, which stays the CLI. It loads the approved
sample, or a generated folder with --data DIR.
"""

import argparse
from pathlib import Path

from pharma_batch_record_checker.demo.app import create_app
from pharma_batch_record_checker.demo.setup import prepare


def main() -> None:
    """Prepare the local database, then serve the review pages."""
    parser = argparse.ArgumentParser(description="Review stored synthetic batches.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--no-setup",
        action="store_true",
        help="Serve only. Do not start Postgres, migrate, or load the sample.",
    )
    parser.add_argument(
        "--data",
        type=Path,
        metavar="DIR",
        help=(
            "load this generated folder (master_record.csv, batch_record.csv), "
            "e.g. from generate-batch-records --out data/, instead of the "
            "approved sample"
        ),
    )
    args = parser.parse_args()
    if args.data is not None and args.no_setup:
        parser.error("--data loads during setup, so it can't be used with --no-setup")
    if args.no_setup:
        pass  # serve whatever is already stored
    elif args.data is None:
        prepare()  # the approved sample, called exactly as before
    else:
        prepare(args.data)
    create_app().run(host=args.host, port=args.port)
