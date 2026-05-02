"""
Load district / sub-district / village hierarchy from districtdata.xlsx.

Tolerant of column variations:
  - Auto-detects sheet (uses first sheet if 'Combined' doesn't exist)
  - Skips duplicate 'District.1' columns
  - Skips empty/Unnamed columns
  - Treats missing 'Flag' as empty
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd
import psycopg2.extras

from db import get_conn

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("load_hierarchy")


REQUIRED_COLS = {"District", "Sub-District", "Village", "State"}


def load_file(path: Path):
    log.info("Reading %s …", path)
    xl = pd.ExcelFile(path)
    log.info("Sheets in workbook: %s", xl.sheet_names)
    # Use 'Combined' if it exists, else first sheet
    sheet = "Combined" if "Combined" in xl.sheet_names else xl.sheet_names[0]
    log.info("Using sheet: %s", sheet)
    df = pd.read_excel(path, sheet_name=sheet, dtype=str)
    log.info("Found %d rows, columns: %s", len(df), list(df.columns))

    missing = REQUIRED_COLS - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {missing}. "
                         f"Got: {list(df.columns)}")

    # Trim whitespace on the columns we care about; nothing else
    for col in ["District", "Sub-District", "Village", "State"]:
        df[col] = df[col].fillna("").astype(str).str.strip()

    # Flag is optional — derive on the fly
    def derive_flag(r):
        missing = []
        if not r["District"]: missing.append("District")
        if not r["Village"]: missing.append("Village")
        return f"MISSING: {', '.join(missing)}" if missing else ""

    df["_flag"] = df.apply(derive_flag, axis=1)

    rows = [
        (
            r["State"] or None,
            r["District"] or None,
            r["Sub-District"] or None,
            r["Village"] or None,
            r["_flag"] or None,
        )
        for _, r in df.iterrows()
    ]

    log.info("Truncating and reloading geographic_hierarchy …")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("TRUNCATE TABLE geographic_hierarchy RESTART IDENTITY")
        psycopg2.extras.execute_values(
            cur,
            "INSERT INTO geographic_hierarchy (state, district, sub_district, village, flag) "
            "VALUES %s",
            rows,
            page_size=2000,
        )
        cur.execute("SELECT COUNT(*) FROM geographic_hierarchy")
        n = cur.fetchone()[0]

    return {
        "rows_loaded": n,
        "districts": df["District"].nunique(),
        "states": df["State"].nunique(),
        "flagged": (df["_flag"] != "").sum(),
    }


def main():
    ap = argparse.ArgumentParser(description="Load geographic hierarchy into Postgres")
    ap.add_argument("--input", type=Path, required=True)
    args = ap.parse_args()

    if not args.input.exists():
        log.error("File not found: %s", args.input)
        sys.exit(1)

    summary = load_file(args.input)

    print("\n" + "=" * 60)
    print("  HIERARCHY LOAD SUMMARY")
    print("=" * 60)
    for k, v in summary.items():
        print(f"  {k:<20} {v:>10,}")
    print("=" * 60)


if __name__ == "__main__":
    main()
