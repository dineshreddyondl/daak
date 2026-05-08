#!/usr/bin/env python3
"""
load_district_centroids.py
==========================

One-time backfill: geocode every (state, district) via Google Geocoding,
store result in district_centroids table.

After this runs once:
  - Lane Identifier endpoint is fully self-contained
  - No further Google calls needed for any lane query

Run BEFORE running this:
  psql ... < district_centroids_schema.sql

Usage:
  # Local
  GOOGLE_MAPS_API_KEY="..." \\
  DATABASE_URL="postgresql://..." \\
      python3 load_district_centroids.py

  # Resume after partial failure (skips districts already in table)
  python3 load_district_centroids.py --resume

  # Re-geocode everything from scratch (deletes existing rows first)
  python3 load_district_centroids.py --refresh

  # Test: just one state
  python3 load_district_centroids.py --state "Andhra Pradesh"

  # Dry run — list what would be geocoded, no API calls, no DB writes
  python3 load_district_centroids.py --dry-run

Cost: 720 districts × $0.005 = ~$3.60 total. Trivial.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Optional

import psycopg2
import requests

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"

# Throttle: well under Google's 50 req/sec limit, easy on shared key
SLEEP_BETWEEN_CALLS_S = 0.05


def connect(url: Optional[str]):
    url = url or os.environ.get("DATABASE_URL") or os.environ.get("DATABASE_PUBLIC_URL")
    if not url:
        sys.exit("Error: no DATABASE_URL set. Pass --url or set env var.")
    return psycopg2.connect(url)


def get_districts_to_geocode(cur, state_filter: Optional[str], skip_existing: bool):
    """
    Build the universe: distinct (state, district) from BOTH source tables.

    UNION'd because:
      - geographic_hierarchy has districts where village data exists
      - pincodes_master has districts where pincode polygons exist
      - either qualifies a district as a valid lane endpoint
    """
    sql = """
        SELECT DISTINCT state, district FROM (
            SELECT state, district FROM geographic_hierarchy
            WHERE state IS NOT NULL AND state != ''
              AND district IS NOT NULL AND district != ''
            UNION
            SELECT state, district FROM pincodes_master
            WHERE state IS NOT NULL AND state != ''
              AND district IS NOT NULL AND district != ''
        ) AS u
    """
    params = []
    if state_filter:
        sql += " WHERE state = %s"
        params.append(state_filter)
    sql += " ORDER BY state, district"
    cur.execute(sql, tuple(params))
    rows = cur.fetchall()

    if not skip_existing:
        return rows

    # Filter out any (state, district) already in district_centroids
    cur.execute("SELECT state, district FROM district_centroids")
    existing = {(s, d) for (s, d) in cur.fetchall()}
    filtered = [(s, d) for (s, d) in rows if (s, d) not in existing]
    return filtered


def geocode_one(api_key: str, state: str, district: str) -> Optional[dict]:
    """
    Call Google Geocoding for a (state, district) pair.
    Returns a dict with lat/lng/bbox/formatted_address/place_id, or None on failure.
    """
    address = f"{district} district, {state}, India"
    params = {
        "address": address,
        "components": "country:IN",
        "key": api_key,
    }

    try:
        r = requests.get(GEOCODE_URL, params=params, timeout=10)
    except requests.RequestException as e:
        print(f"  network error for {state}/{district}: {e}", file=sys.stderr)
        return None

    if r.status_code != 200:
        print(f"  HTTP {r.status_code} for {state}/{district}: {r.text[:200]}", file=sys.stderr)
        return None

    data = r.json()
    status = data.get("status")
    if status == "ZERO_RESULTS":
        print(f"  ZERO_RESULTS for {state}/{district}", file=sys.stderr)
        return None
    if status == "OVER_QUERY_LIMIT":
        print(f"  ⚠ OVER_QUERY_LIMIT — backing off 60s", file=sys.stderr)
        time.sleep(60)
        return None
    if status != "OK":
        print(f"  status={status} for {state}/{district}: {data.get('error_message', '')}",
              file=sys.stderr)
        return None

    results = data.get("results") or []
    if not results:
        return None

    best = results[0]
    geom = best.get("geometry") or {}
    loc = geom.get("location") or {}
    viewport = geom.get("viewport") or {}
    ne = viewport.get("northeast") or {}
    sw = viewport.get("southwest") or {}

    if "lat" not in loc or "lng" not in loc:
        return None

    return {
        "lat": loc["lat"],
        "lng": loc["lng"],
        "bbox_ne_lat": ne.get("lat"),
        "bbox_ne_lng": ne.get("lng"),
        "bbox_sw_lat": sw.get("lat"),
        "bbox_sw_lng": sw.get("lng"),
        "formatted_address": best.get("formatted_address"),
        "place_id": best.get("place_id"),
    }


UPSERT_SQL = """
    INSERT INTO district_centroids
        (state, district, lat, lng,
         bbox_ne_lat, bbox_ne_lng, bbox_sw_lat, bbox_sw_lng,
         formatted_address, place_id)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (state, district) DO UPDATE
       SET lat = EXCLUDED.lat,
           lng = EXCLUDED.lng,
           bbox_ne_lat = EXCLUDED.bbox_ne_lat,
           bbox_ne_lng = EXCLUDED.bbox_ne_lng,
           bbox_sw_lat = EXCLUDED.bbox_sw_lat,
           bbox_sw_lng = EXCLUDED.bbox_sw_lng,
           formatted_address = EXCLUDED.formatted_address,
           place_id = EXCLUDED.place_id,
           geocoded_at = now()
"""


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--url", help="Postgres URL (overrides DATABASE_URL).")
    p.add_argument("--api-key", help="Google Maps API key (overrides GOOGLE_MAPS_API_KEY env).")
    p.add_argument("--state", help="Limit to a single state.")
    p.add_argument("--resume", action="store_true",
                   help="Skip districts already in district_centroids.")
    p.add_argument("--refresh", action="store_true",
                   help="DELETE all existing rows first, then re-geocode everything.")
    p.add_argument("--dry-run", action="store_true",
                   help="List districts that would be geocoded, do nothing else.")
    args = p.parse_args()

    api_key = args.api_key or os.environ.get("GOOGLE_MAPS_API_KEY")
    if not args.dry_run and not api_key:
        sys.exit("Error: GOOGLE_MAPS_API_KEY env var or --api-key required.")

    print("Connecting…")
    conn = connect(args.url)
    cur = conn.cursor()

    # Sanity check the table exists
    cur.execute("SELECT to_regclass('district_centroids')")
    if cur.fetchone()[0] is None:
        sys.exit("Error: district_centroids table not found. Run district_centroids_schema.sql first.")

    if args.refresh:
        if args.state:
            cur.execute("DELETE FROM district_centroids WHERE state = %s", (args.state,))
            print(f"Deleted {cur.rowcount} existing rows for state={args.state}.")
        else:
            cur.execute("DELETE FROM district_centroids")
            print(f"Deleted {cur.rowcount} existing rows.")
        conn.commit()

    skip_existing = args.resume and not args.refresh
    todo = get_districts_to_geocode(cur, args.state, skip_existing)
    total = len(todo)
    print(f"Districts to geocode: {total}")

    if args.dry_run:
        for s, d in todo[:20]:
            print(f"  {s} / {d}")
        if total > 20:
            print(f"  ... ({total - 20} more)")
        print("\nDRY RUN — no API calls, no DB writes.")
        return 0

    if total == 0:
        print("Nothing to do.")
        return 0

    print(f"Estimated cost: ~${total * 0.005:.2f}")
    print(f"Estimated time: ~{total * (SLEEP_BETWEEN_CALLS_S + 0.15):.0f}s")
    print()

    success = 0
    failed = []

    for i, (state, district) in enumerate(todo, start=1):
        if i % 25 == 0 or i == 1:
            print(f"  [{i}/{total}] {state} / {district}", flush=True)

        result = geocode_one(api_key, state, district)
        if result is None:
            failed.append((state, district))
        else:
            cur.execute(UPSERT_SQL, (
                state, district,
                result["lat"], result["lng"],
                result["bbox_ne_lat"], result["bbox_ne_lng"],
                result["bbox_sw_lat"], result["bbox_sw_lng"],
                result["formatted_address"], result["place_id"],
            ))
            conn.commit()
            success += 1

        time.sleep(SLEEP_BETWEEN_CALLS_S)

    print()
    print("─" * 50)
    print(f"DONE")
    print(f"  Success: {success} / {total}")
    print(f"  Failed:  {len(failed)}")
    if failed:
        print(f"  First 10 failures:")
        for s, d in failed[:10]:
            print(f"    - {s} / {d}")
        print(f"  Re-run with --resume to retry.")

    cur.close()
    conn.close()
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())