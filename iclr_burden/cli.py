import argparse
import json
import sys
import sqlite3
from urllib.error import URLError

from .constants import SCORE_VERSION
from .errors import BurdenError
from .database import (DEFAULT_DATABASE, build_database, calculate_all, database_status,
                       get_author_report, get_paper_record, search_authors, top_a, top_s)
from .dataset_source import YEARS, download_release, extract_release
from .profiles import public_output
from .storage import canonical_json, write_json_new


def _emit(value, output):
    value = public_output(value)
    if output:
        write_json_new(output, value)
        print(str(output))
    else:
        sys.stdout.write(canonical_json(value).decode("utf-8"))


def parser():
    root = argparse.ArgumentParser(description="ICLR Review Burden Rating: local dataset, scores, and query UI.")
    root.add_argument("--version", action="version", version=SCORE_VERSION)
    commands = root.add_subparsers(dest="command", required=True)
    author = commands.add_parser("author", help="Look up one author in the local database")
    author.add_argument("--author", required=True, help="OpenReview Profile ID, e.g. ~Given_Family1")
    author.add_argument("--db", default=DEFAULT_DATABASE)
    author.add_argument("--output")
    search = commands.add_parser("search", help="Search authors by name or Profile ID")
    search.add_argument("query")
    search.add_argument("--db", default=DEFAULT_DATABASE)
    search.add_argument("--limit", type=int, default=20)
    top_s_cmd = commands.add_parser("top-s", help="Top authors by peak annual score")
    top_s_cmd.add_argument("--k", type=int, default=10, help="1–100 authors")
    top_s_cmd.add_argument("--db", default=DEFAULT_DATABASE)
    top_s_cmd.add_argument("--output")
    top_a_cmd = commands.add_parser("top-a", help="Top authors by annual score for one year")
    top_a_cmd.add_argument("--year", type=int, required=True, help="ICLR year, e.g. 2026")
    top_a_cmd.add_argument("--k", type=int, default=10, help="1–100 authors")
    top_a_cmd.add_argument("--db", default=DEFAULT_DATABASE)
    top_a_cmd.add_argument("--output")
    serve = commands.add_parser("serve", help="Open the local query UI")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--db", default=DEFAULT_DATABASE)
    paper = commands.add_parser("paper", help="Look up one paper")
    paper.add_argument("--paper", required=True)
    paper.add_argument("--db", default=DEFAULT_DATABASE)
    paper.add_argument("--output")
    status = commands.add_parser("db-status", help="Show local database status")
    status.add_argument("--db", default=DEFAULT_DATABASE)
    status.add_argument("--output")
    compute = commands.add_parser("compute-all", help="Recompute paper and author scores")
    compute.add_argument("--db", default=DEFAULT_DATABASE)
    compute.add_argument("--output")
    compute.add_argument("--replace-score-version", action="store_true",
                         help="Overwrite scores stored under a different formula version")
    setup = commands.add_parser("dataset-setup", help="Download the pinned dump, import it, and score")
    setup.add_argument("--source", default="data/source")
    setup.add_argument("--db", default=DEFAULT_DATABASE)
    setup.add_argument("--years", type=int, nargs="+", default=list(YEARS))
    setup.add_argument("--output")
    setup.add_argument("--skip-compute", action="store_true")
    setup.add_argument("--archive", default="data/downloads/iclr-v1.0.tar.bz2")
    setup.add_argument("--replace-score-version", action="store_true")
    imported = commands.add_parser("dataset-import", help="Import local JSONL and score")
    imported.add_argument("--source", default="data/source")
    imported.add_argument("--db", default=DEFAULT_DATABASE)
    imported.add_argument("--years", type=int, nargs="+", default=list(YEARS))
    imported.add_argument("--output")
    imported.add_argument("--skip-compute", action="store_true")
    imported.add_argument("--source-date", required=True, help="Snapshot date in ISO 8601")
    imported.add_argument("--replace-score-version", action="store_true")
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        progress = lambda message: print(message, file=sys.stderr, flush=True)
        if args.command in {"dataset-setup", "dataset-import"}:
            if args.command == "dataset-setup":
                archive = download_release(args.archive, progress=progress)
                extract_release(archive, args.source, progress=progress)
            kwargs = {"source_date": args.source_date} if args.command == "dataset-import" else {}
            result = {"import": build_database(args.source, args.db, years=args.years, progress=progress, **kwargs)}
            if not args.skip_compute:
                result["calculation"] = calculate_all(
                    args.db, progress=progress, replace_score_version=args.replace_score_version)
            _emit(result, args.output)
            return 0
        if args.command == "compute-all":
            _emit(calculate_all(args.db, progress=progress, replace_score_version=args.replace_score_version),
                  args.output)
            return 0
        if args.command == "serve":
            from .webapp import serve_app
            serve_app(host=args.host, port=args.port, db_path=args.db)
            return 0
        if args.command == "db-status":
            _emit(database_status(args.db), args.output)
            return 0
        if args.command == "search":
            _emit(search_authors(args.query, args.db, limit=args.limit), None)
            return 0
        if args.command == "top-s":
            _emit(top_s(k=args.k, db_path=args.db), args.output)
            return 0
        if args.command == "top-a":
            _emit(top_a(args.year, k=args.k, db_path=args.db), args.output)
            return 0
        if args.command == "paper":
            _emit(get_paper_record(args.paper, args.db), args.output)
            return 0
        if args.command == "author":
            _emit(get_author_report(args.author, args.db), args.output)
            return 0
        raise BurdenError(f"Unknown command: {args.command}")
    except (BurdenError, OSError, json.JSONDecodeError, sqlite3.Error, URLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
