"""
daak — FastAPI backend
"""

from __future__ import annotations

import io
import json
import math
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException, Body, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import openpyxl


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


@contextmanager
def get_conn(dict_cursor: bool = True):
    conn = psycopg2.connect(get_database_url())
    if dict_cursor:
        conn.cursor_factory = RealDictCursor
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def fetch_all(query: str, params: tuple = ()) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchall()


def fetch_one(query: str, params: tuple = ()) -> Optional[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchone()


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R, to_rad = 6371.0, math.pi / 180
    d_lat = (lat2 - lat1) * to_rad
    d_lng = (lng2 - lng1) * to_rad
    a = (math.sin(d_lat / 2) ** 2
         + math.cos(lat1 * to_rad) * math.cos(lat2 * to_rad)
         * math.sin(d_lng / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))


app = FastAPI(title="daak API", version="0.3.0")

# CORS — read allowed origins from env (comma-separated), with sensible defaults
_default_origins = "http://localhost:5173,http://localhost:3000"
_allowed_origins = os.environ.get("CORS_ORIGINS", _default_origins)
allowed_origins = [o.strip() for o in _allowed_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class HubIn(BaseModel):
    name: str = Field(..., min_length=1)
    lat: float
    lng: float
    radius_km: float = Field(..., gt=0, le=200)
    placement_method: Optional[str] = None
    place_name: Optional[str] = None
    sub_district: Optional[str] = None
    village: Optional[str] = None


class FinalizeIn(BaseModel):
    finalized_by: Optional[str] = "admin"


class ToggleIn(BaseModel):
    is_excluded: bool
    reason: Optional[str] = None


@app.get("/api/health")
def health():
    try:
        row = fetch_one("SELECT 1 AS ok")
        return {"status": "ok", "db": row["ok"] == 1}
    except Exception as e:
        raise HTTPException(503, f"DB unhealthy: {e}")


@app.get("/api/states")
def list_states():
    rows = fetch_all("""
        SELECT DISTINCT state FROM geographic_hierarchy
        WHERE state IS NOT NULL AND state != ''
        ORDER BY state
    """)
    return [r["state"] for r in rows]


@app.get("/api/districts")
def list_districts(state: str = Query(...)):
    rows = fetch_all("""
        SELECT DISTINCT district FROM geographic_hierarchy
        WHERE state = %s AND district IS NOT NULL AND district != ''
        ORDER BY district
    """, (state,))
    return [r["district"] for r in rows]


@app.get("/api/sub_districts")
def list_sub_districts(state: str = Query(...), district: str = Query(...)):
    rows = fetch_all("""
        SELECT DISTINCT sub_district FROM geographic_hierarchy
        WHERE state = %s AND district = %s
          AND sub_district IS NOT NULL AND sub_district != ''
        ORDER BY sub_district
    """, (state, district))
    return [r["sub_district"] for r in rows]


@app.get("/api/villages")
def list_villages(state: str = Query(...), district: str = Query(...),
                  sub_district: Optional[str] = Query(None)):
    if sub_district:
        rows = fetch_all("""
            SELECT DISTINCT village FROM geographic_hierarchy
            WHERE state = %s AND district = %s AND sub_district = %s
              AND village IS NOT NULL AND village != ''
            ORDER BY village
        """, (state, district, sub_district))
    else:
        rows = fetch_all("""
            SELECT DISTINCT village FROM geographic_hierarchy
            WHERE state = %s AND district = %s
              AND village IS NOT NULL AND village != ''
            ORDER BY village
        """, (state, district))
    return [r["village"] for r in rows]


@app.get("/api/search")
def search(
    q: str = Query(..., min_length=1, max_length=100, description="Search query"),
    types: str = Query(
        "district,sub_district,village,pincode",
        description="Comma-separated entity types to include"
    ),
    limit: int = Query(20, ge=1, le=100),
):
    """
    Mixed-type search across districts, sub-districts, villages, and pincodes.

    Ranking (lower rank_score = better match):
      0 = exact match (case-insensitive)
      1 = prefix match
      2 = contains match

    Results are ordered by rank, then by entity type
    (district > sub_district > village > pincode), then alphabetically.
    """
    q_clean = q.strip()
    q_lower = q_clean.lower()

    # Parse and validate types filter
    allowed = {"district", "sub_district", "village", "pincode"}
    requested = sorted({t.strip() for t in types.split(",") if t.strip()} & allowed)
    if not requested:
        return {"query": q_clean, "count": 0, "results": []}

    types_placeholder = ",".join(["%s"] * len(requested))

    # Pincode prefix matches only make sense for numeric queries
    is_numeric = q_clean.isdigit()
    pincode_clause = (
        "(entity_type = 'pincode' AND pincode LIKE %s)"
        if is_numeric else "FALSE"
    )

    sql = """
        SELECT
            entity_type,
            name,
            state,
            district,
            sub_district,
            pincode,
            centroid_lat,
            centroid_lng,
            metadata,
            CASE
                WHEN name_lower = %s THEN 0
                WHEN name_lower LIKE %s THEN 1
                ELSE 2
            END AS rank_score,
            CASE entity_type
                WHEN 'district'     THEN 0
                WHEN 'sub_district' THEN 1
                WHEN 'village'      THEN 2
                WHEN 'pincode'      THEN 3
            END AS type_priority
        FROM search_index
        WHERE entity_type IN ({types_placeholder})
          AND (
            name_lower ILIKE %s
            OR {pincode_clause}
          )
        ORDER BY rank_score ASC, type_priority ASC, name ASC
        LIMIT %s
    """.format(
        types_placeholder=types_placeholder,
        pincode_clause=pincode_clause,
    )

    params = [q_lower, q_lower + "%"]      # rank_score CASE
    params.extend(requested)               # IN (...) types
    params.append("%" + q_lower + "%")     # name ILIKE
    if is_numeric:
        params.append(q_clean + "%")       # pincode LIKE
    params.append(limit)

    rows = fetch_all(sql, tuple(params))

    # Shape for frontend: build parent_path, drop ranking-only fields
    for r in rows:
        parts = []
        if r["entity_type"] in ("sub_district", "village", "pincode"):
            if r.get("state"):
                parts.append(r["state"])
            if r.get("district"):
                parts.append(r["district"])
        if r["entity_type"] == "village" and r.get("sub_district"):
            parts.append(r["sub_district"])
        r["parent_path"] = " · ".join(parts) if parts else None
        r.pop("rank_score", None)
        r.pop("type_priority", None)

    return {
        "query": q_clean,
        "count": len(rows),
        "results": rows,
    }


# ─── Bulk lookup ──────────────────────────────────────────────────────────────

BULK_MAX_NAMES = 500


class BulkSearchIn(BaseModel):
    names: list[str] = Field(..., min_length=1, max_length=BULK_MAX_NAMES)
    type_override: Optional[str] = Field(
        None,
        description="Force a single entity_type for all names. None = auto-detect.",
    )


def _bulk_search_one(name: str, type_override: Optional[str]) -> dict:
    """
    Look up one name, return best match + up to 5 alternatives + confidence.
    Returns a dict shaped for the bulk results table.
    """
    raw = (name or "").strip()
    if not raw:
        return _na_row(name)

    q_lower = raw.lower()

    # Decide which entity_types to search
    if type_override and type_override in {"district", "sub_district", "village", "pincode"}:
        types = [type_override]
    else:
        # Auto-detect: numeric → pincode only, otherwise all types
        types = ["pincode"] if raw.isdigit() else ["district", "sub_district", "village", "pincode"]

    types_placeholder = ",".join(["%s"] * len(types))
    is_numeric = raw.isdigit()
    pincode_clause = (
        "(entity_type = 'pincode' AND pincode LIKE %s)"
        if is_numeric else "FALSE"
    )

    sql = """
        SELECT
            entity_type, name, state, district, sub_district, pincode,
            centroid_lat, centroid_lng, metadata,
            CASE
                WHEN name_lower = %s THEN 0
                WHEN name_lower LIKE %s THEN 1
                ELSE 2
            END AS rank_score,
            CASE entity_type
                WHEN 'district'     THEN 0
                WHEN 'sub_district' THEN 1
                WHEN 'village'      THEN 2
                WHEN 'pincode'      THEN 3
            END AS type_priority
        FROM search_index
        WHERE entity_type IN ({types_placeholder})
          AND (
            name_lower ILIKE %s
            OR {pincode_clause}
          )
        ORDER BY rank_score ASC, type_priority ASC, name ASC
        LIMIT 6
    """.format(
        types_placeholder=types_placeholder,
        pincode_clause=pincode_clause,
    )

    params = [q_lower, q_lower + "%"]
    params.extend(types)
    params.append("%" + q_lower + "%")
    if is_numeric:
        params.append(raw + "%")

    rows = fetch_all(sql, tuple(params))

    if not rows:
        return _na_row(name)

    best = rows[0]
    confidence = {0: "exact", 1: "prefix", 2: "partial"}.get(best["rank_score"], "partial")

    def _shape(r: dict) -> dict:
        return {
            "entity_type": r["entity_type"],
            "name": r["name"],
            "state": r["state"],
            "district": r["district"],
            "sub_district": r["sub_district"],
            "pincode": r["pincode"],
        }

    alternatives = [_shape(r) for r in rows[1:]]
    return {
        "input": name,
        "status": "matched",
        "confidence": confidence,
        "best_match": _shape(best),
        "alternatives": alternatives,
    }


def _na_row(name: str) -> dict:
    return {
        "input": name,
        "status": "not_found",
        "confidence": "none",
        "best_match": {
            "entity_type": "NA",
            "name": "NA",
            "state": "NA",
            "district": "NA",
            "sub_district": "NA",
            "pincode": "NA",
        },
        "alternatives": [],
    }


@app.post("/api/search/bulk")
def search_bulk(body: BulkSearchIn):
    """
    Look up many names at once. Cap at 500.
    Returns one row per input name, with best match + alternatives.
    """
    type_override = body.type_override
    if type_override and type_override not in {"district", "sub_district", "village", "pincode"}:
        raise HTTPException(400, f"Invalid type_override: {type_override}")

    results = [_bulk_search_one(n, type_override) for n in body.names]

    matched = sum(1 for r in results if r["status"] == "matched")
    return {
        "count": len(results),
        "matched": matched,
        "not_found": len(results) - matched,
        "results": results,
    }


@app.post("/api/search/bulk/export")
def search_bulk_export(body: BulkSearchIn):
    """
    Same input as /api/search/bulk but returns an .xlsx file directly.
    Used by the frontend's 'Download as XLSX' button.
    """
    type_override = body.type_override
    results = [_bulk_search_one(n, type_override) for n in body.names]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Bulk Lookup"

    headers = [
        "#", "Input", "Status", "Confidence",
        "Type", "Matched Name", "State", "District", "Sub-district", "Pincode",
        "Alternatives Count",
    ]
    ws.append(headers)

    for i, r in enumerate(results, start=1):
        bm = r["best_match"]
        ws.append([
            i,
            r["input"],
            r["status"],
            r["confidence"],
            bm.get("entity_type", "NA"),
            bm.get("name", "NA"),
            bm.get("state", "NA"),
            bm.get("district", "NA"),
            bm.get("sub_district", "NA") if bm.get("sub_district") else "NA",
            bm.get("pincode", "NA") if bm.get("pincode") else "NA",
            len(r.get("alternatives", [])),
        ])

    # Optional second sheet listing alternatives, only for rows that have them
    alt_sheet = wb.create_sheet("Alternatives")
    alt_sheet.append([
        "Row #", "Input", "Type", "Name", "State", "District", "Sub-district", "Pincode",
    ])
    for i, r in enumerate(results, start=1):
        for alt in r.get("alternatives", []):
            alt_sheet.append([
                i,
                r["input"],
                alt.get("entity_type", "NA"),
                alt.get("name", "NA"),
                alt.get("state", "NA"),
                alt.get("district", "NA"),
                alt.get("sub_district", "NA") if alt.get("sub_district") else "NA",
                alt.get("pincode", "NA") if alt.get("pincode") else "NA",
            ])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="daak_bulk_lookup_{ts}.xlsx"',
        },
    )


@app.get("/api/district/stats")
def district_stats(state: str = Query(...), district: str = Query(...)):
    row = fetch_one("""
        SELECT
            (SELECT COUNT(*) FROM pincodes_master
             WHERE LOWER(district) = LOWER(%s)) AS pincodes,
            (SELECT COUNT(*) FROM pincodes_master
             WHERE LOWER(district) = LOWER(%s) AND has_polygon) AS pincodes_with_polygon,
            (SELECT COUNT(DISTINCT sub_district) FROM geographic_hierarchy
             WHERE state = %s AND district = %s) AS sub_districts,
            (SELECT COUNT(*) FROM geographic_hierarchy
             WHERE state = %s AND district = %s) AS villages,
            (SELECT status FROM district_status WHERE district = %s) AS status
    """, (district, district, state, district, state, district, district))
    return row


@app.get("/api/district/pincodes")
def district_pincodes(district: str = Query(...)):
    rows = fetch_all("""
        SELECT pincode, city, district, state,
               centroid_lat, centroid_lng,
               boundary_geojson, has_polygon
        FROM pincodes_master
        WHERE LOWER(district) = LOWER(%s)
          AND centroid_lat IS NOT NULL AND centroid_lng IS NOT NULL
        ORDER BY pincode
    """, (district,))
    for r in rows:
        b = r.get("boundary_geojson")
        if isinstance(b, str):
            try:
                r["boundary_geojson"] = json.loads(b)
            except (json.JSONDecodeError, TypeError):
                r["boundary_geojson"] = None
    return rows


@app.get("/api/district/hubs")
def list_hubs(state: str = Query(...), district: str = Query(...)):
    hubs = fetch_all("""
        SELECT id, district, state, hub_name, hub_lat, hub_lng, radius_km,
               placement_method, place_name, sub_district, village,
               created_by, created_at
        FROM district_hubs
        WHERE state = %s AND district = %s
        ORDER BY id
    """, (state, district))

    if not hubs:
        return []

    hub_ids = tuple(h["id"] for h in hubs)
    cov_rows = fetch_all("""
        SELECT id, hub_id, pincode, city, state, distance_km,
               is_excluded, exclusion_reason
        FROM district_coverage
        WHERE hub_id IN %s
        ORDER BY hub_id, distance_km
    """, (hub_ids,))

    by_hub: dict[int, list] = {h["id"]: [] for h in hubs}
    for r in cov_rows:
        by_hub[r["hub_id"]].append(r)
    for h in hubs:
        h["pincodes"] = by_hub.get(h["id"], [])
    return hubs


@app.post("/api/district/{state}/{district}/hubs")
def create_hub(state: str, district: str, hub: HubIn):
    status_row = fetch_one("SELECT status FROM district_status WHERE district = %s",
                           (district,))
    if status_row and status_row["status"] == "FINALIZED":
        raise HTTPException(400, f"District '{district}' is FINALIZED — read-only")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO district_status (district, state, status)
                VALUES (%s, %s, 'DRAFT')
                ON CONFLICT (district) DO NOTHING
            """, (district, state))

            cur.execute("""
                INSERT INTO district_hubs
                  (district, state, hub_name, hub_lat, hub_lng, radius_km,
                   placement_method, place_name, sub_district, village, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, created_at
            """, (district, state, hub.name, hub.lat, hub.lng, hub.radius_km,
                  hub.placement_method, hub.place_name, hub.sub_district, hub.village,
                  "admin"))
            new = cur.fetchone()
            hub_id = new["id"]
            created_at = new["created_at"]

            cur.execute("""
                SELECT pincode, city, state, centroid_lat, centroid_lng
                FROM pincodes_master
                WHERE LOWER(district) = LOWER(%s)
                  AND centroid_lat IS NOT NULL
            """, (district,))
            candidates = cur.fetchall()

            cov_inserts = []
            for p in candidates:
                d = haversine_km(hub.lat, hub.lng, p["centroid_lat"], p["centroid_lng"])
                if d <= hub.radius_km:
                    cov_inserts.append((
                        hub_id, district, p["pincode"], p["city"] or "",
                        p["state"], round(d, 2),
                    ))

            if cov_inserts:
                from psycopg2.extras import execute_values
                execute_values(cur, """
                    INSERT INTO district_coverage
                      (hub_id, district, pincode, city, state, distance_km)
                    VALUES %s
                """, cov_inserts)

    return {
        "id": hub_id,
        "name": hub.name,
        "lat": hub.lat, "lng": hub.lng,
        "radius_km": hub.radius_km,
        "pincodes_found": len(cov_inserts),
        "created_at": created_at.isoformat() if created_at else None,
    }


@app.delete("/api/hub/{hub_id}")
def delete_hub(hub_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT district FROM district_hubs WHERE id = %s", (hub_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Hub not found")
            district = row["district"]

            cur.execute("SELECT status FROM district_status WHERE district = %s",
                        (district,))
            s = cur.fetchone()
            if s and s["status"] == "FINALIZED":
                raise HTTPException(400, "District is FINALIZED — cannot delete hubs")

            cur.execute("DELETE FROM district_hubs WHERE id = %s", (hub_id,))
    return {"deleted": hub_id}


@app.post("/api/coverage/{coverage_id}/toggle")
def toggle_coverage(coverage_id: int, body: ToggleIn):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT c.district FROM district_coverage c WHERE c.id = %s",
                        (coverage_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Coverage row not found")
            district = row["district"]

            cur.execute("SELECT status FROM district_status WHERE district = %s",
                        (district,))
            s = cur.fetchone()
            if s and s["status"] == "FINALIZED":
                raise HTTPException(400, "District is FINALIZED — read-only")

            if body.is_excluded:
                cur.execute("""
                    UPDATE district_coverage
                    SET is_excluded = TRUE, excluded_by = 'admin',
                        excluded_at = NOW(), exclusion_reason = %s
                    WHERE id = %s
                """, (body.reason, coverage_id))
            else:
                cur.execute("""
                    UPDATE district_coverage
                    SET is_excluded = FALSE, excluded_by = NULL,
                        excluded_at = NULL, exclusion_reason = NULL
                    WHERE id = %s
                """, (coverage_id,))
    return {"id": coverage_id, "is_excluded": body.is_excluded}


@app.post("/api/coverage/{coverage_id}/exclude")
def exclude_pincode_legacy(coverage_id: int, reason: Optional[str] = Body(None, embed=True)):
    return toggle_coverage(coverage_id, ToggleIn(is_excluded=True, reason=reason))


@app.post("/api/district/{state}/{district}/finalize")
def finalize_district(state: str, district: str, body: FinalizeIn):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO district_status (district, state, status,
                                              finalized_by, finalized_at, updated_at)
                VALUES (%s, %s, 'FINALIZED', %s, NOW(), NOW())
                ON CONFLICT (district) DO UPDATE
                SET status = 'FINALIZED',
                    finalized_by = EXCLUDED.finalized_by,
                    finalized_at = NOW(),
                    updated_at   = NOW()
            """, (district, state, body.finalized_by))
    return {"district": district, "status": "FINALIZED"}


@app.delete("/api/district/{state}/{district}")
def delete_district(state: str, district: str):
    """
    Hard-delete a district's working data: status, hubs, and coverage.

    This ONLY removes ops-created rows from:
      - district_coverage  (pincodes assigned to this district's hubs)
      - district_hubs      (hubs ops placed)
      - district_status    (DRAFT / FINALIZED tracking)

    Master data is NEVER touched: pincodes_master, geographic_hierarchy,
    and search_index remain intact. After delete the district disappears
    from Serviceability and the user can start over in Builder.

    Allowed for both DRAFT and FINALIZED districts.
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            # Confirm something exists to delete
            cur.execute(
                "SELECT status FROM district_status WHERE district = %s AND state = %s",
                (district, state),
            )
            if cur.fetchone() is None:
                raise HTTPException(404, f"District '{district}' not found in {state}")

            # Count what's about to go away (for the response)
            cur.execute("""
                SELECT
                    (SELECT COUNT(*) FROM district_hubs
                       WHERE district = %s AND state = %s) AS hub_count,
                    (SELECT COUNT(*) FROM district_coverage c
                       JOIN district_hubs h ON h.id = c.hub_id
                       WHERE h.district = %s AND h.state = %s) AS coverage_count
            """, (district, state, district, state))
            counts = cur.fetchone()

            # Delete in order: coverage -> hubs -> status
            # (district_coverage has a FK to district_hubs)
            cur.execute("""
                DELETE FROM district_coverage
                WHERE hub_id IN (
                    SELECT id FROM district_hubs
                    WHERE district = %s AND state = %s
                )
            """, (district, state))

            cur.execute(
                "DELETE FROM district_hubs WHERE district = %s AND state = %s",
                (district, state),
            )

            cur.execute(
                "DELETE FROM district_status WHERE district = %s AND state = %s",
                (district, state),
            )

    return {
        "deleted": True,
        "district": district,
        "state": state,
        "removed_hubs": counts["hub_count"],
        "removed_coverage_rows": counts["coverage_count"],
    }


@app.get("/api/serviceability/overview")
def serviceability_overview(state: Optional[str] = None,
                            status: Optional[str] = None,
                            search: Optional[str] = None):
    where_clauses = ["EXISTS (SELECT 1 FROM district_hubs h WHERE h.district = ds.district)"]
    params: list = []

    if state:
        where_clauses.append("ds.state = %s")
        params.append(state)
    if status:
        where_clauses.append("ds.status = %s")
        params.append(status.upper())
    if search:
        where_clauses.append("LOWER(ds.district) LIKE %s")
        params.append(f"%{search.lower()}%")

    where_sql = " AND ".join(where_clauses)

    rows = fetch_all(f"""
        SELECT
            ds.district, ds.state, ds.status,
            ds.finalized_by, ds.finalized_at, ds.updated_at,
            (SELECT COUNT(*) FROM district_hubs h WHERE h.district = ds.district) AS hub_count,
            (SELECT COUNT(DISTINCT c.pincode) FROM district_coverage c
              JOIN district_hubs h ON h.id = c.hub_id
              WHERE h.district = ds.district AND c.is_excluded = FALSE
            ) AS active_pincodes,
            (SELECT COUNT(DISTINCT c.pincode) FROM district_coverage c
              JOIN district_hubs h ON h.id = c.hub_id
              WHERE h.district = ds.district AND c.is_excluded = TRUE
            ) AS excluded_pincodes
        FROM district_status ds
        WHERE {where_sql}
        ORDER BY ds.state, ds.district
    """, tuple(params))
    return rows


@app.get("/api/serviceability/summary")
def serviceability_summary():
    row = fetch_one("""
        SELECT
            (SELECT COUNT(*) FROM district_status ds
              WHERE EXISTS (SELECT 1 FROM district_hubs h WHERE h.district = ds.district)
            ) AS total_districts,
            (SELECT COUNT(*) FROM district_status ds
              WHERE ds.status = 'DRAFT'
                AND EXISTS (SELECT 1 FROM district_hubs h WHERE h.district = ds.district)
            ) AS draft_districts,
            (SELECT COUNT(*) FROM district_status ds
              WHERE ds.status = 'FINALIZED'
            ) AS finalized_districts,
            (SELECT COUNT(*) FROM district_hubs) AS total_hubs,
            (SELECT COUNT(*) FROM district_coverage WHERE is_excluded = FALSE) AS active_coverage_rows,
            (SELECT COUNT(*) FROM district_coverage WHERE is_excluded = TRUE)  AS excluded_coverage_rows
    """)
    return row


@app.get("/api/serviceability/district")
def serviceability_district(state: str = Query(...), district: str = Query(...)):
    status_row = fetch_one("SELECT * FROM district_status WHERE district = %s",
                           (district,))
    if not status_row:
        raise HTTPException(404, "District not in serviceability")

    hubs = fetch_all("""
        SELECT id, hub_name, hub_lat, hub_lng, radius_km, created_at
        FROM district_hubs
        WHERE state = %s AND district = %s
        ORDER BY id
    """, (state, district))

    coverage = fetch_all("""
        SELECT c.id, c.pincode, c.city, c.distance_km, c.is_excluded,
               c.exclusion_reason, h.hub_name AS hub_name
        FROM district_coverage c
        JOIN district_hubs h ON h.id = c.hub_id
        WHERE h.state = %s AND h.district = %s
        ORDER BY c.pincode, c.distance_km
    """, (state, district))

    return {"status": status_row, "hubs": hubs, "coverage": coverage}


@app.get("/api/districts/finalized")
def list_finalized_districts():
    rows = fetch_all("""
        SELECT district, state, finalized_by, finalized_at,
               (SELECT COUNT(DISTINCT pincode) FROM v_district_active_pincodes
                WHERE district = ds.district) AS unique_pincodes
        FROM district_status ds
        WHERE status = 'FINALIZED'
        ORDER BY finalized_at DESC
    """)
    return rows


def _safe_sheet_name(name: str) -> str:
    bad = set(r'[]:*?/\\')
    cleaned = "".join("_" if c in bad else c for c in name)
    return cleaned[:31] or "Sheet"


def _build_multi_tab_excel(districts: list[dict]) -> bytes:
    wb = openpyxl.Workbook()

    ws = wb.active
    ws.title = "Overview"
    ws.append(["State", "District", "Status", "Hubs", "Active Pincodes",
               "Excluded Pincodes", "Finalized By", "Finalized At"])
    for d in districts:
        ws.append([
            d["state"], d["district"], d["status"],
            d["hub_count"], d.get("active_pincodes", 0),
            d.get("excluded_pincodes", 0),
            d.get("finalized_by"),
            d["finalized_at"].strftime("%Y-%m-%d %H:%M") if d.get("finalized_at") else "",
        ])

    used_names: set[str] = set()
    for d in districts:
        base = _safe_sheet_name(d["district"])
        name = base
        i = 2
        while name in used_names:
            suffix = f"_{i}"
            name = (base[: 31 - len(suffix)] + suffix)
            i += 1
        used_names.add(name)

        sheet = wb.create_sheet(title=name)
        sheet.append([f"{d['district']}, {d['state']}"])
        sheet.append([f"Status: {d['status']} · "
                      f"{d['hub_count']} hubs · "
                      f"{d.get('active_pincodes', 0)} active pincodes · "
                      f"{d.get('excluded_pincodes', 0)} excluded"])
        sheet.append([])
        sheet.append(["Hubs"])
        sheet.append(["Hub Name", "Lat", "Lng", "Radius (km)", "Created At"])
        for h in d.get("hubs", []):
            sheet.append([
                h["hub_name"], h["hub_lat"], h["hub_lng"], h["radius_km"],
                h["created_at"].strftime("%Y-%m-%d %H:%M") if h.get("created_at") else "",
            ])
        sheet.append([])
        sheet.append(["Pincode Coverage"])
        sheet.append(["Pincode", "City", "Hub", "Distance (km)", "Status",
                      "Exclusion Reason"])
        for c in d.get("coverage", []):
            sheet.append([
                c["pincode"], c.get("city", ""), c.get("hub_name", ""),
                c.get("distance_km"),
                "EXCLUDED" if c.get("is_excluded") else "ACTIVE",
                c.get("exclusion_reason") or "",
            ])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.read()


def _gather_districts_for_export(filter_status: Optional[str]) -> list[dict]:
    where = ""
    params: list = []
    if filter_status:
        where = "WHERE ds.status = %s"
        params.append(filter_status)

    base_rows = fetch_all(f"""
        SELECT
            ds.district, ds.state, ds.status,
            ds.finalized_by, ds.finalized_at,
            (SELECT COUNT(*) FROM district_hubs h WHERE h.district = ds.district) AS hub_count,
            (SELECT COUNT(DISTINCT c.pincode) FROM district_coverage c
              JOIN district_hubs h ON h.id = c.hub_id
              WHERE h.district = ds.district AND c.is_excluded = FALSE
            ) AS active_pincodes,
            (SELECT COUNT(DISTINCT c.pincode) FROM district_coverage c
              JOIN district_hubs h ON h.id = c.hub_id
              WHERE h.district = ds.district AND c.is_excluded = TRUE
            ) AS excluded_pincodes
        FROM district_status ds
        {where}
        ORDER BY ds.state, ds.district
    """, tuple(params))

    base_rows = [r for r in base_rows if r["hub_count"] > 0]

    for d in base_rows:
        d["hubs"] = fetch_all("""
            SELECT id, hub_name, hub_lat, hub_lng, radius_km, created_at
            FROM district_hubs
            WHERE district = %s
            ORDER BY id
        """, (d["district"],))
        d["coverage"] = fetch_all("""
            SELECT c.pincode, c.city, c.distance_km, c.is_excluded,
                   c.exclusion_reason, h.hub_name
            FROM district_coverage c
            JOIN district_hubs h ON h.id = c.hub_id
            WHERE h.district = %s
            ORDER BY c.is_excluded, c.pincode
        """, (d["district"],))

    return base_rows


@app.get("/api/export/district")
def export_district(district: str = Query(...)):
    rows = _gather_districts_for_export(filter_status=None)
    rows = [r for r in rows if r["district"].lower() == district.lower()]
    if not rows:
        raise HTTPException(404, "No data for this district")
    data = _build_multi_tab_excel(rows)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="daak_{district}_{ts}.xlsx"'},
    )


@app.get("/api/export/drafts")
def export_drafts():
    rows = _gather_districts_for_export(filter_status="DRAFT")
    if not rows:
        raise HTTPException(404, "No DRAFT districts to export")
    data = _build_multi_tab_excel(rows)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="daak_drafts_{ts}.xlsx"'},
    )


@app.get("/api/export/finalized")
def export_finalized():
    rows = _gather_districts_for_export(filter_status="FINALIZED")
    if not rows:
        raise HTTPException(404, "No FINALIZED districts to export")
    data = _build_multi_tab_excel(rows)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="daak_finalized_{ts}.xlsx"'},
    )


@app.get("/api/export/all")
def export_all_districts_legacy():
    return export_finalized()


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)