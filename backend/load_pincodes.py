"""
Load pincode master data from your MongoDB JSON export into Postgres.

Usage:
    python load_pincodes.py --input path/to/utils-db.masterpincodes.json

Behavior:
    - Validates each record (skips ones missing pincode/centroid)
    - Detects whether to UPDATE existing rows or INSERT new ones
    - Reports a summary: inserted / updated / skipped / errors
    - Idempotent: running twice produces the same final state
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg2.extras

from db import get_conn

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("load_pincodes")


def parse_record(raw: dict) -> dict | None:
    """Convert a MongoDB pincode JSON record into a flat row, or None if invalid."""
    pincode = raw.get("pincode")
    if not pincode:
        return None

    geom = raw.get("geometry") or {}
    centroid = geom.get("centroid")
    if not centroid:
        return None

    try:
        c_lat = float(centroid["lat"])
        c_lng = float(centroid["lng"])
    except (TypeError, ValueError, KeyError):
        return None

    bbox = geom.get("boundingBox") or {}
    try:
        ne_lat = float(bbox["ne"]["lat"]) if bbox.get("ne") else None
        ne_lng = float(bbox["ne"]["lng"]) if bbox.get("ne") else None
        sw_lat = float(bbox["sw"]["lat"]) if bbox.get("sw") else None
        sw_lng = float(bbox["sw"]["lng"]) if bbox.get("sw") else None
    except (TypeError, ValueError, KeyError):
        ne_lat = ne_lng = sw_lat = sw_lng = None

    boundary = geom.get("boundary")
    has_polygon = bool(boundary and boundary.get("type") == "Polygon")

    # Mongo's $date format → Python datetime (best effort)
    src_updated = None
    upd = raw.get("updatedAt")
    if isinstance(upd, dict) and "$date" in upd:
        try:
            src_updated = datetime.fromisoformat(upd["$date"].replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass

    return {
        "pincode": str(pincode).strip(),
        "city": (raw.get("city") or "").strip() or None,
        "district": (raw.get("district") or "").strip() or None,
        "state": (raw.get("state") or "").strip() or None,
        "country": (raw.get("country") or "India").strip() or "India",
        "centroid_lat": c_lat,
        "centroid_lng": c_lng,
        "bbox_ne_lat": ne_lat,
        "bbox_ne_lng": ne_lng,
        "bbox_sw_lat": sw_lat,
        "bbox_sw_lng": sw_lng,
        "boundary_geojson": json.dumps(boundary) if boundary else None,
        "has_polygon": has_polygon,
        "source_updated_at": src_updated,
    }


UPSERT_SQL = """
INSERT INTO pincodes_master (
    pincode, city, district, state, country,
    centroid_lat, centroid_lng,
    bbox_ne_lat, bbox_ne_lng, bbox_sw_lat, bbox_sw_lng,
    boundary_geojson, has_polygon, source_updated_at, loaded_at
) VALUES %s
ON CONFLICT (pincode) DO UPDATE SET
    city              = EXCLUDED.city,
    district          = EXCLUDED.district,
    state             = EXCLUDED.state,
    country           = EXCLUDED.country,
    centroid_lat      = EXCLUDED.centroid_lat,
    centroid_lng      = EXCLUDED.centroid_lng,
    bbox_ne_lat       = EXCLUDED.bbox_ne_lat,
    bbox_ne_lng       = EXCLUDED.bbox_ne_lng,
    bbox_sw_lat       = EXCLUDED.bbox_sw_lat,
    bbox_sw_lng       = EXCLUDED.bbox_sw_lng,
    boundary_geojson  = EXCLUDED.boundary_geojson,
    has_polygon       = EXCLUDED.has_polygon,
    source_updated_at = EXCLUDED.source_updated_at,
    loaded_at         = NOW()
"""


def load_file(path: Path, batch_size: int = 1000) -> dict:
    """Stream-load the JSON file into pincodes_master."""
    log.info("Reading %s …", path)
    with open(path, "r") as f:
        data = json.load(f)
    log.info("Found %d raw records", len(data))

    # Parse and validate
    rows = []
    skipped = 0
    for raw in data:
        parsed = parse_record(raw)
        if parsed is None:
            skipped += 1
            continue
        rows.append(parsed)

    log.info("Parsed %d valid rows; skipped %d invalid", len(rows), skipped)

    # Bulk upsert in batches
    inserted_or_updated = 0
    errors = 0

    with get_conn() as conn:
        cur = conn.cursor()
        # Get existing pincode count before upsert (for "inserted" estimate)
        cur.execute("SELECT COUNT(*) FROM pincodes_master")
        before_count = cur.fetchone()[0]

        for i in range(0, len(rows), batch_size):
            batch = rows[i:i + batch_size]
            try:
                psycopg2.extras.execute_values(
                    cur, UPSERT_SQL,
                    [
                        (
                            r["pincode"], r["city"], r["district"], r["state"], r["country"],
                            r["centroid_lat"], r["centroid_lng"],
                            r["bbox_ne_lat"], r["bbox_ne_lng"], r["bbox_sw_lat"], r["bbox_sw_lng"],
                            r["boundary_geojson"], r["has_polygon"], r["source_updated_at"],
                            datetime.utcnow(),
                        )
                        for r in batch
                    ],
                    page_size=batch_size,
                )
                inserted_or_updated += len(batch)
                log.info("  upserted batch %d / %d (%d rows)",
                         i // batch_size + 1, (len(rows) + batch_size - 1) // batch_size, len(batch))
            except Exception as e:
                log.error("Batch failed: %s", e)
                errors += len(batch)
                conn.rollback()
                # Re-acquire cursor for next batch
                cur = conn.cursor()

        cur.execute("SELECT COUNT(*) FROM pincodes_master")
        after_count = cur.fetchone()[0]

    summary = {
        "total_in_file": len(data),
        "valid_rows": len(rows),
        "skipped_invalid": skipped,
        "upserted": inserted_or_updated,
        "errors": errors,
        "rows_before": before_count,
        "rows_after": after_count,
        "net_new": after_count - before_count,
    }
    return summary


def main():
    ap = argparse.ArgumentParser(description="Load pincode master JSON into Postgres")
    ap.add_argument("--input", type=Path, required=True, help="Path to JSON file (MongoDB export)")
    args = ap.parse_args()

    if not args.input.exists():
        log.error("File not found: %s", args.input)
        sys.exit(1)

    summary = load_file(args.input)

    print("\n" + "=" * 60)
    print("  PINCODE MASTER LOAD SUMMARY")
    print("=" * 60)
    for k, v in summary.items():
        print(f"  {k:<22} {v:>10,}")
    print("=" * 60)


if __name__ == "__main__":
    main()
