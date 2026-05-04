#!/usr/bin/env python3
"""
load_search_index.py
====================

Populates the `search_index` table from existing curated tables:
  - pincodes_master       → 'pincode' rows + 'district' rows (distinct)
  - geographic_hierarchy  → 'district' rows + 'sub_district' rows + 'village' rows

Existing tables are NOT modified. This script only writes to search_index.

Run BEFORE running this:
  psql ... < search_schema.sql

Usage:
  # Local
  python load_search_index.py

  # Railway (set DATABASE_URL or pass --url)
  DATABASE_URL="$RAILWAY_DB" python load_search_index.py

  # Dry run (counts only, no inserts)
  python load_search_index.py --dry-run

The script is idempotent: it TRUNCATEs and re-inserts every row.
Re-run any time pincodes_master / geographic_hierarchy change.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

import psycopg2
from psycopg2.extras import Json, execute_batch


def connect(url: str | None):
    url = url or os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    if not url:
        sys.exit("Error: no DATABASE_URL set. Pass --url or set env var.")
    return psycopg2.connect(url)


def fetch_districts(cur):
    """One row per (state, district). Source: geographic_hierarchy (canonical names)."""
    cur.execute("""
        SELECT DISTINCT state, district
        FROM geographic_hierarchy
        WHERE state IS NOT NULL AND district IS NOT NULL
    """)
    return cur.fetchall()


def fetch_sub_districts(cur):
    """One row per (state, district, sub_district)."""
    cur.execute("""
        SELECT DISTINCT state, district, sub_district
        FROM geographic_hierarchy
        WHERE state IS NOT NULL
          AND district IS NOT NULL
          AND sub_district IS NOT NULL
    """)
    return cur.fetchall()


def fetch_villages(cur):
    """One row per (state, district, sub_district, village)."""
    cur.execute("""
        SELECT DISTINCT state, district, sub_district, village
        FROM geographic_hierarchy
        WHERE state IS NOT NULL
          AND district IS NOT NULL
          AND sub_district IS NOT NULL
          AND village IS NOT NULL
    """)
    return cur.fetchall()


def fetch_pincodes(cur):
    """One row per pincode. Pulls centroid lat/lng for the map preview."""
    cur.execute("""
        SELECT
            pincode,
            state,
            district,
            city,
            centroid_lat,
            centroid_lng
        FROM pincodes_master
        WHERE pincode IS NOT NULL
    """)
    return cur.fetchall()


INSERT_SQL = """
    INSERT INTO search_index
        (entity_type, name, name_lower, state, district, sub_district, pincode,
         centroid_lat, centroid_lng, metadata)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--url", help="Postgres URL (overrides DATABASE_URL).")
    p.add_argument("--dry-run", action="store_true", help="Count rows only, do not insert.")
    p.add_argument("--batch-size", type=int, default=1000, help="Insert batch size.")
    args = p.parse_args()

    print("Connecting…")
    conn = connect(args.url)
    cur = conn.cursor()

    # ── Sanity-check that search_index exists ───────────────────────────────
    cur.execute("SELECT to_regclass('search_index')")
    if cur.fetchone()[0] is None:
        sys.exit("Error: search_index table not found. Run search_schema.sql first.")

    # ── Pull source rows ────────────────────────────────────────────────────
    print("Fetching source rows…")
    t0 = time.time()
    districts = fetch_districts(cur)
    sub_districts = fetch_sub_districts(cur)
    villages = fetch_villages(cur)
    pincodes = fetch_pincodes(cur)
    print(f"  districts:      {len(districts):>7,}")
    print(f"  sub_districts:  {len(sub_districts):>7,}")
    print(f"  villages:       {len(villages):>7,}")
    print(f"  pincodes:       {len(pincodes):>7,}")
    print(f"  fetched in {time.time() - t0:.1f}s")

    if args.dry_run:
        total = len(districts) + len(sub_districts) + len(villages) + len(pincodes)
        print(f"\nDRY RUN — would insert {total:,} rows. Skipping.")
        return 0

    # ── Build insert tuples ─────────────────────────────────────────────────
    rows = []

    # Empty-metadata sentinel for non-pincode rows
    EMPTY_META = Json({})

    for state, district in districts:
        rows.append((
            "district", district, district.lower(),
            state, district, None, None,
            None, None, EMPTY_META,
        ))

    for state, district, sub_district in sub_districts:
        rows.append((
            "sub_district", sub_district, sub_district.lower(),
            state, district, sub_district, None,
            None, None, EMPTY_META,
        ))

    for state, district, sub_district, village in villages:
        rows.append((
            "village", village, village.lower(),
            state, district, sub_district, None,
            None, None, EMPTY_META,
        ))

    for pincode, state, district, city, lat, lng in pincodes:
        # city goes into metadata so search results can show it inline
        meta = Json({"city": city}) if city else Json({})
        rows.append((
            "pincode", str(pincode), str(pincode).lower(),
            state, district, None, str(pincode),
            float(lat) if lat is not None else None,
            float(lng) if lng is not None else None,
            meta,
        ))

    # ── Replace contents of search_index in a single transaction ────────────
    print(f"\nLoading {len(rows):,} rows into search_index…")
    t0 = time.time()
    try:
        cur.execute("TRUNCATE search_index RESTART IDENTITY")
        execute_batch(cur, INSERT_SQL, rows, page_size=args.batch_size)
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    print(f"  inserted in {time.time() - t0:.1f}s")

    # ── Quick verification ──────────────────────────────────────────────────
    cur.execute("""
        SELECT entity_type, COUNT(*)
        FROM search_index
        GROUP BY entity_type
        ORDER BY entity_type
    """)
    print("\nFinal row counts:")
    for entity_type, n in cur.fetchall():
        print(f"  {entity_type:<14} {n:>7,}")

    cur.close()
    conn.close()
    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())