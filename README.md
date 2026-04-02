# OpenAlex Journal Citation Network Parser

This repository contains a CLI parser that builds a citation network for papers published in a list of journals (OpenAlex source IDs).

## What it does

- Estimates API cost before crawling.
- Pulls full work payloads from OpenAlex (no `select`, so all available fields are captured).
- Persists both:
  - raw JSON payloads for reprocessing/auditing,
  - normalized tables for querying (works, citations, funders, awards).
- Supports resumable cursor-based pagination.

## Requirements

- Python 3.10+
- SQLite (bundled with Python)

## Usage

### Config file (recommended)

You can place common parameters (including journal list) in a JSON file.

Example `params.json`:

```json
{
  "api_key": "YOUR_OPENALEX_API_KEY",
  "email": "you@example.com",
  "journals": ["S4210209073", "S123456789"],
  "db": "data/openalex_citations.db",
  "delay_s": 0.1
}
```

You can copy the repository example file and edit it:

```bash
cp params.example.json params.json
```

### 1) Cost estimate

```bash
python openalex_parser.py estimate \
  --config params.json
```

### 2) Crawl and persist network data

```bash
python openalex_parser.py crawl \
  --config params.json
```

You can still override config values with CLI flags, for example:

```bash
python openalex_parser.py crawl --config params.json --db another.db --delay-s 0
```

## Notes

- The crawler requests up to 100 works per page and uses OpenAlex cursor paging.
- Funding data is captured from `funders` and `awards` fields.
- Citation edges are captured from `referenced_works`.

## How output data is stored

Output is stored in a SQLite database (`--db`, default `openalex_citations.db`):

- `works_raw`: full raw work JSON payloads (audit/reprocess source of truth)
- `works`: normalized core work metadata
- `citations`: citation edges (`src_work_id -> dst_work_id`)
- `work_funders`: normalized funding organizations per work
- `work_awards`: normalized award/grant records per work
- `crawl_runs`: run-level status metadata
- `journal_cursors`: per-journal cursor checkpoints (resumable crawl)
- `fetch_log`: page-level fetch logs

Detailed DB schema and table relationships are documented in:

- `docs/database_schema.md`
