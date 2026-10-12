"""Command line helpers: `python -m app.cli auth` and `python -m app.cli sync`."""

import argparse
import logging
import sys

from elasticsearch import Elasticsearch

from . import drive
from .config import get_settings
from .sync import run_sync


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    parser.add_argument("command", choices=["auth", "sync"])
    args = parser.parse_args()
    settings = get_settings()
    logging.basicConfig(level=logging.INFO)

    if args.command == "auth":
        drive.authorize(
            settings.google_credentials_file, settings.google_token_file, settings.oauth_port
        )
        print(f"Saved Google token to {settings.google_token_file}")
    else:
        es = Elasticsearch(settings.elasticsearch_url)
        stats, errors = run_sync(es, settings)
        print(stats.as_dict())
        if errors:
            sys.exit("\n".join(errors))


if __name__ == "__main__":
    main()
