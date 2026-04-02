#!/usr/bin/env python3
"""Quick sanity checks for parsed OpenAlex citation data."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check parsed OpenAlex SQLite data")
    parser.add_argument(
        "--db",
        default="data/openalex_citations.db",
        help="Path to SQLite database (default: data/openalex_citations.db)",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=5,
        help="How many sample works to print (default: 5)",
    )
    return parser.parse_args()


def table_count(conn: sqlite3.Connection, table_name: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
    return int(row[0]) if row else 0


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)

    if not db_path.exists():
        raise SystemExit(f"Database not found: {db_path}")

    conn = sqlite3.connect(str(db_path))
    try:
        print(f"DB: {db_path}")
        print()

        checks = [
            ("works", "works"),
            ("works_raw", "works_raw"),
            ("citations", "citations"),
            ("work_funders", "work_funders"),
            ("work_awards", "work_awards"),
            ("crawl_runs", "crawl_runs"),
            ("fetch_log", "fetch_log"),
        ]
        print("Table counts")
        for label, table in checks:
            print(f"  {label:12s} {table_count(conn, table)}")

        print()
        print("Latest crawl run")
        latest_run = conn.execute(
            """
            SELECT id, started_at, finished_at, status, notes
            FROM crawl_runs
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        if latest_run is None:
            print("  (no crawl runs found)")
        else:
            run_id, started_at, finished_at, status, notes = latest_run
            print(f"  run_id:     {run_id}")
            print(f"  started_at: {started_at}")
            print(f"  finished_at:{' ' if finished_at else ''}{finished_at or '(null)'}")
            print(f"  status:     {status}")
            print(f"  notes:      {notes or '(none)'}")

        print()
        print(f"Sample works (up to {args.sample_size})")
        rows = conn.execute(
            """
            SELECT work_id, publication_year, COALESCE(display_name, title, '')
            FROM works
            ORDER BY last_seen_at DESC
            LIMIT ?
            """,
            (args.sample_size,),
        ).fetchall()
        if not rows:
            print("  (no works found)")
        else:
            for work_id, pub_year, name in rows:
                trimmed = (name[:100] + "...") if len(name) > 100 else name
                print(f"  - {work_id} | year={pub_year} | {trimmed}")

        print()
        run_status = latest_run[3] if latest_run else None
        works_n = table_count(conn, "works")
        citations_n = table_count(conn, "citations")
        healthy = run_status == "completed" and works_n > 0 and citations_n > 0
        print(f"Quick health check: {'PASS' if healthy else 'CHECK'}")
        print("  Criteria: latest run completed, works > 0, citations > 0")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
