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
        raise RuntimeError("DATABASE_URL is not set. Create a .env file.")
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
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


# ---------------------------------------------------------------------------
# Read endpoints (Builder)
# ---------------------------------------------------------------------------

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
                  sub_district: str = Query(...)):
    rows = fetch_all("""
        SELECT DISTINCT village FROM geographic_hierarchy
        WHERE state = %s AND district = %s AND sub_district = %s
          AND village IS NOT NULL AND village != ''
        ORDER BY village
    """, (state, district, sub_district))
    return [r["village"] for r in rows]


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
            cur.execute("""
                SELECT c.district FROM district_coverage c
                WHERE c.id = %s
            """, (coverage_id,))
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


# ---------------------------------------------------------------------------
# Serviceability — list / detail / aggregate
# ---------------------------------------------------------------------------

@app.get("/api/serviceability/overview")
def serviceability_overview(state: Optional[str] = None,
                            status: Optional[str] = None,
                            search: Optional[str] = None):
    """List of all districts that have at least 1 hub, with summary counts.

    Filters: state, status (DRAFT/FINALIZED), search (substring of district name).
    """
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
    """Top-line counts across all districts with at least 1 hub."""
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
    """Read-only detail for a saved district: hubs + per-pincode coverage."""
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

    return {
        "status": status_row,
        "hubs": hubs,
        "coverage": coverage,
    }


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


# ---------------------------------------------------------------------------
# Excel export — multi-tab
# ---------------------------------------------------------------------------

def _safe_sheet_name(name: str) -> str:
    """Excel sheet names must be <=31 chars, no special chars."""
    bad = set(r'[]:*?/\\')
    cleaned = "".join("_" if c in bad else c for c in name)
    return cleaned[:31] or "Sheet"


def _build_multi_tab_excel(districts: list[dict]) -> bytes:
    """One tab per district + an Overview tab at the front.

    Each `district` dict must have:
      - district, state, status, hub_count, active_pincodes
      - hubs: list of dicts
      - coverage: list of dicts
    """
    wb = openpyxl.Workbook()

    # Overview tab
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
        # Ensure unique sheet names
        base = _safe_sheet_name(d["district"])
        name = base
        i = 2
        while name in used_names:
            suffix = f"_{i}"
            name = (base[: 31 - len(suffix)] + suffix)
            i += 1
        used_names.add(name)

        sheet = wb.create_sheet(title=name)

        # Header rows summarizing the district
        sheet.append([f"{d['district']}, {d['state']}"])
        sheet.append([f"Status: {d['status']} · "
                      f"{d['hub_count']} hubs · "
                      f"{d.get('active_pincodes', 0)} active pincodes · "
                      f"{d.get('excluded_pincodes', 0)} excluded"])
        sheet.append([])

        # Hubs section
        sheet.append(["Hubs"])
        sheet.append(["Hub Name", "Lat", "Lng", "Radius (km)", "Created At"])
        for h in d.get("hubs", []):
            sheet.append([
                h["hub_name"], h["hub_lat"], h["hub_lng"], h["radius_km"],
                h["created_at"].strftime("%Y-%m-%d %H:%M") if h.get("created_at") else "",
            ])
        sheet.append([])

        # Coverage section
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
    """Return enriched district list with hubs + coverage embedded."""
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

    # Skip districts with zero hubs
    base_rows = [r for r in base_rows if r["hub_count"] > 0]

    # For each district, hydrate hubs + coverage
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
    """Single-district Excel — same multi-section format as multi-tab, but one sheet."""
    rows = _gather_districts_for_export(filter_status=None)
    rows = [r for r in rows if r["district"].lower() == district.lower()]
    if not rows:
        raise HTTPException(404, "No data for this district")
    data = _build_multi_tab_excel(rows)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"daak_{district}_{ts}.xlsx"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/export/drafts")
def export_drafts():
    """One tab per DRAFT district."""
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
    """One tab per FINALIZED district."""
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


# Backwards compat — old clients calling /api/export/all get FINALIZED
@app.get("/api/export/all")
def export_all_districts_legacy():
    return export_finalized()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)