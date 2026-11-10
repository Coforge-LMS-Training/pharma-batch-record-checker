"""Run the review demo.

The default bind is 127.0.0.1:8000. This command is separate from
pharma-batch-record-checker, which stays the CLI.
"""

import argparse

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
    args = parser.parse_args()
    if not args.no_setup:
        prepare()
    create_app().run(host=args.host, port=args.port)
