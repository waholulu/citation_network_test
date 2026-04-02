#!/usr/bin/env python3
"""Build citation networks for journal-scoped works via OpenAlex."""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

OPENALEX_BASE = "https://api.openalex.org"


@dataclass
class OpenAlexClient:
    api_key: str | None = None
    email: str | None = None
    base_url: str = OPENALEX_BASE
    timeout: int = 60

    def _build_url(self, path: str, params: dict[str, Any] | None = None) -> str:
        query_params: dict[str, Any] = {}
        if params:
            query_params.update({k: v for k, v in params.items() if v is not None})
        if self.api_key:
            query_params["api_key"] = self.api_key
        if self.email:
            query_params["mailto"] = self.email
        qs = urlencode(query_params, doseq=True)
        return f"{self.base_url}{path}?{qs}" if qs else f"{self.base_url}{path}"

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = self._build_url(path, params)
        req = Request(url, headers={"User-Agent": "openalex-citation-network-parser/1.0"})
        with urlopen(req, timeout=self.timeout) as resp:
            body = resp.read().decode("utf-8")
        return json.loads(body)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_config(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;

        CREATE TABLE IF NOT EXISTS crawl_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            journals_json TEXT NOT NULL,
            status TEXT NOT NULL,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS journal_cursors (
            run_id INTEGER NOT NULL,
            journal_id TEXT NOT NULL,
            next_cursor TEXT,
            completed INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (run_id, journal_id)
        );

        CREATE TABLE IF NOT EXISTS works_raw (
            work_id TEXT PRIMARY KEY,
            fetched_at TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            payload_hash INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS works (
            work_id TEXT PRIMARY KEY,
            title TEXT,
            display_name TEXT,
            doi TEXT,
            publication_year INTEGER,
            publication_date TEXT,
            work_type TEXT,
            language TEXT,
            cited_by_count INTEGER,
            source_id TEXT,
            source_display_name TEXT,
            updated_date TEXT,
            created_date TEXT,
            record_json TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS citations (
            src_work_id TEXT NOT NULL,
            dst_work_id TEXT NOT NULL,
            discovered_at TEXT NOT NULL,
            PRIMARY KEY (src_work_id, dst_work_id)
        );

        CREATE TABLE IF NOT EXISTS work_funders (
            work_id TEXT NOT NULL,
            funder_id TEXT,
            funder_display_name TEXT,
            funder_country_code TEXT,
            funder_type TEXT,
            raw_json TEXT NOT NULL,
            PRIMARY KEY (work_id, funder_id)
        );

        CREATE TABLE IF NOT EXISTS work_awards (
            work_id TEXT NOT NULL,
            award_id TEXT,
            funder_id TEXT,
            funder_display_name TEXT,
            funder_award_id TEXT,
            award_doi TEXT,
            raw_json TEXT NOT NULL,
            PRIMARY KEY (work_id, award_id, funder_award_id)
        );

        CREATE TABLE IF NOT EXISTS fetch_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            journal_id TEXT NOT NULL,
            cursor_in TEXT,
            cursor_out TEXT,
            page_results INTEGER NOT NULL,
            fetched_at TEXT NOT NULL,
            cost_usd REAL
        );
        """
    )


def normalize_source(work: dict[str, Any]) -> tuple[str | None, str | None]:
    primary_location = work.get("primary_location") or {}
    source = primary_location.get("source") or {}
    return source.get("id"), source.get("display_name")


def upsert_work(conn: sqlite3.Connection, work: dict[str, Any]) -> None:
    work_id = work.get("id")
    if not work_id:
        return

    record_json = json.dumps(work, ensure_ascii=False)
    payload_hash = hash(record_json)
    now = utc_now_iso()
    source_id, source_name = normalize_source(work)

    conn.execute(
        """
        INSERT INTO works_raw (work_id, fetched_at, payload_json, payload_hash)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(work_id) DO UPDATE SET
            fetched_at=excluded.fetched_at,
            payload_json=excluded.payload_json,
            payload_hash=excluded.payload_hash
        """,
        (work_id, now, record_json, payload_hash),
    )

    conn.execute(
        """
        INSERT INTO works (
            work_id, title, display_name, doi, publication_year, publication_date,
            work_type, language, cited_by_count, source_id, source_display_name,
            updated_date, created_date, record_json, last_seen_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(work_id) DO UPDATE SET
            title=excluded.title,
            display_name=excluded.display_name,
            doi=excluded.doi,
            publication_year=excluded.publication_year,
            publication_date=excluded.publication_date,
            work_type=excluded.work_type,
            language=excluded.language,
            cited_by_count=excluded.cited_by_count,
            source_id=excluded.source_id,
            source_display_name=excluded.source_display_name,
            updated_date=excluded.updated_date,
            created_date=excluded.created_date,
            record_json=excluded.record_json,
            last_seen_at=excluded.last_seen_at
        """,
        (
            work_id,
            work.get("title"),
            work.get("display_name"),
            work.get("doi"),
            work.get("publication_year"),
            work.get("publication_date"),
            work.get("type"),
            work.get("language"),
            work.get("cited_by_count"),
            source_id,
            source_name,
            work.get("updated_date"),
            work.get("created_date"),
            record_json,
            now,
        ),
    )

    for dst_work_id in work.get("referenced_works") or []:
        conn.execute(
            """
            INSERT OR IGNORE INTO citations (src_work_id, dst_work_id, discovered_at)
            VALUES (?, ?, ?)
            """,
            (work_id, dst_work_id, now),
        )

    for funder in work.get("funders") or []:
        conn.execute(
            """
            INSERT INTO work_funders (
                work_id, funder_id, funder_display_name, funder_country_code, funder_type, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(work_id, funder_id) DO UPDATE SET
                funder_display_name=excluded.funder_display_name,
                funder_country_code=excluded.funder_country_code,
                funder_type=excluded.funder_type,
                raw_json=excluded.raw_json
            """,
            (
                work_id,
                funder.get("id"),
                funder.get("display_name"),
                funder.get("country_code"),
                funder.get("type"),
                json.dumps(funder, ensure_ascii=False),
            ),
        )

    for award in work.get("awards") or []:
        conn.execute(
            """
            INSERT INTO work_awards (
                work_id, award_id, funder_id, funder_display_name,
                funder_award_id, award_doi, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(work_id, award_id, funder_award_id) DO UPDATE SET
                funder_id=excluded.funder_id,
                funder_display_name=excluded.funder_display_name,
                award_doi=excluded.award_doi,
                raw_json=excluded.raw_json
            """,
            (
                work_id,
                award.get("id"),
                award.get("funder") or award.get("funder_id"),
                award.get("funder_display_name"),
                award.get("funder_award_id"),
                award.get("doi"),
                json.dumps(award, ensure_ascii=False),
            ),
        )


def estimate_cost(client: OpenAlexClient, journals: list[str], per_page: int = 100) -> list[dict[str, Any]]:
    estimates: list[dict[str, Any]] = []
    for journal_id in journals:
        response = client.get_json(
            "/works",
            params={
                "filter": f"primary_location.source.id:{journal_id}",
                "per_page": per_page,
                "cursor": "*",
            },
        )
        total_works = int(response.get("meta", {}).get("count", 0))
        page_calls = math.ceil(total_works / per_page) if total_works else 0
        assumed_cost_per_call = 0.0001
        estimated_cost_usd = page_calls * assumed_cost_per_call
        estimates.append(
            {
                "journal_id": journal_id,
                "total_works": total_works,
                "page_calls": page_calls,
                "estimated_cost_usd": round(estimated_cost_usd, 6),
            }
        )
    return estimates


def run_crawl(client: OpenAlexClient, db_path: str, journals: list[str], delay_s: float = 0.0) -> int:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    init_db(conn)

    run_id = conn.execute(
        "INSERT INTO crawl_runs (started_at, journals_json, status) VALUES (?, ?, ?)",
        (utc_now_iso(), json.dumps(journals), "running"),
    ).lastrowid
    conn.commit()

    try:
        for journal_id in journals:
            cursor = "*"
            while True:
                response = client.get_json(
                    "/works",
                    params={
                        "filter": f"primary_location.source.id:{journal_id}",
                        "per_page": 100,
                        "cursor": cursor,
                    },
                )
                results = response.get("results") or []
                meta = response.get("meta") or {}
                next_cursor = meta.get("next_cursor")
                fetched_at = utc_now_iso()

                with conn:
                    for work in results:
                        upsert_work(conn, work)

                    conn.execute(
                        """
                        INSERT INTO fetch_log (
                            run_id, journal_id, cursor_in, cursor_out, page_results, fetched_at, cost_usd
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (run_id, journal_id, cursor, next_cursor, len(results), fetched_at, None),
                    )

                    conn.execute(
                        """
                        INSERT INTO journal_cursors (run_id, journal_id, next_cursor, completed, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(run_id, journal_id) DO UPDATE SET
                            next_cursor=excluded.next_cursor,
                            completed=excluded.completed,
                            updated_at=excluded.updated_at
                        """,
                        (run_id, journal_id, next_cursor, 0, fetched_at),
                    )

                if not next_cursor or not results:
                    with conn:
                        conn.execute(
                            """
                            UPDATE journal_cursors
                            SET completed=1, updated_at=?
                            WHERE run_id=? AND journal_id=?
                            """,
                            (utc_now_iso(), run_id, journal_id),
                        )
                    break

                cursor = next_cursor
                if delay_s > 0:
                    time.sleep(delay_s)

        with conn:
            conn.execute(
                "UPDATE crawl_runs SET finished_at=?, status=? WHERE id=?",
                (utc_now_iso(), "completed", run_id),
            )
    except Exception as exc:  # noqa: BLE001
        with conn:
            conn.execute(
                "UPDATE crawl_runs SET finished_at=?, status=?, notes=? WHERE id=?",
                (utc_now_iso(), "failed", str(exc), run_id),
            )
        raise
    finally:
        conn.close()

    return run_id


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OpenAlex journal citation network parser")
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--config",
        default=None,
        help="Path to JSON config file, e.g. {\"api_key\":..., \"email\":..., \"journals\":[...]}",
    )
    common.add_argument("--api-key", default=None, help="OpenAlex API key")
    common.add_argument("--email", default=None, help="Contact email for polite pool")

    estimate_parser = subparsers.add_parser("estimate", parents=[common], help="Estimate cost")
    estimate_parser.add_argument("--journals", nargs="+", required=False, help="OpenAlex source IDs")

    crawl_parser = subparsers.add_parser("crawl", parents=[common], help="Run crawler")
    crawl_parser.add_argument("--journals", nargs="+", required=False, help="OpenAlex source IDs")
    crawl_parser.add_argument("--db", default=None, help="SQLite db path")
    crawl_parser.add_argument("--delay-s", type=float, default=None, help="Delay between page requests")

    return parser.parse_args()


def merge_runtime_config(args: argparse.Namespace) -> dict[str, Any]:
    file_cfg = load_config(args.config)
    merged: dict[str, Any] = {}
    merged["api_key"] = args.api_key if args.api_key is not None else file_cfg.get("api_key")
    merged["email"] = args.email if args.email is not None else file_cfg.get("email")
    merged["journals"] = args.journals if args.journals is not None else file_cfg.get("journals")
    if args.command == "crawl":
        merged["db"] = args.db if args.db is not None else file_cfg.get("db", "openalex_citations.db")
        merged["delay_s"] = args.delay_s if args.delay_s is not None else float(file_cfg.get("delay_s", 0.0))
    return merged


def main() -> None:
    args = parse_args()
    cfg = merge_runtime_config(args)
    journals = cfg.get("journals") or []
    if not journals:
        raise ValueError("No journals provided. Use --journals or set journals in --config JSON.")

    if not isinstance(journals, list):
        raise ValueError("journals must be a JSON list in config file.")

    client = OpenAlexClient(api_key=cfg.get("api_key"), email=cfg.get("email"))

    if args.command == "estimate":
        estimates = estimate_cost(client, journals)
        total_cost = sum(item["estimated_cost_usd"] for item in estimates)
        output = {
            "generated_at": utc_now_iso(),
            "assumed_cost_per_call_usd": 0.0001,
            "journals": estimates,
            "total_estimated_cost_usd": round(total_cost, 6),
        }
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return

    if args.command == "crawl":
        db_path = cfg.get("db") or "openalex_citations.db"
        os.makedirs(os.path.dirname(db_path), exist_ok=True) if os.path.dirname(db_path) else None
        run_id = run_crawl(client, db_path, journals, delay_s=float(cfg.get("delay_s", 0.0)))
        print(json.dumps({"run_id": run_id, "db": db_path}, indent=2))
        return

    raise ValueError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    main()
