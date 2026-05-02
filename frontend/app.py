"""
daak — Hub Coverage Planner
Step 4: Show pincode polygons on a map for the selected district.
"""

import json

import folium
import streamlit as st
from streamlit_folium import st_folium

from db import fetch_all

st.set_page_config(
    page_title="daak — Hub Coverage Planner",
    page_icon="🚚",
    layout="wide",
)

st.title("🚚 daak")
st.caption("Hub Coverage Planner")


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300)
def get_states() -> list[str]:
    rows = fetch_all("""
        SELECT DISTINCT state FROM geographic_hierarchy
        WHERE state IS NOT NULL AND state != ''
        ORDER BY state
    """)
    return [r["state"] for r in rows]


@st.cache_data(ttl=300)
def get_districts(state: str) -> list[str]:
    rows = fetch_all("""
        SELECT DISTINCT district FROM geographic_hierarchy
        WHERE state = %s AND district IS NOT NULL AND district != ''
        ORDER BY district
    """, (state,))
    return [r["district"] for r in rows]


@st.cache_data(ttl=60)
def get_district_stats(state: str, district: str) -> dict:
    rows = fetch_all("""
        SELECT
            (SELECT COUNT(*) FROM pincodes_master
             WHERE LOWER(district) = LOWER(%s)) AS pincodes,
            (SELECT COUNT(*) FROM pincodes_master
             WHERE LOWER(district) = LOWER(%s) AND has_polygon) AS pincodes_with_polygon,
            (SELECT COUNT(DISTINCT sub_district) FROM geographic_hierarchy
             WHERE state = %s AND district = %s) AS sub_districts,
            (SELECT COUNT(*) FROM geographic_hierarchy
             WHERE state = %s AND district = %s) AS villages
    """, (district, district, state, district, state, district))
    return rows[0]


@st.cache_data(ttl=300)
def get_district_pincodes(district: str) -> list[dict]:
    """All pincode records for the given district, including geometry."""
    rows = fetch_all("""
        SELECT pincode, city, district, state,
               centroid_lat, centroid_lng,
               boundary_geojson, has_polygon
        FROM pincodes_master
        WHERE LOWER(district) = LOWER(%s)
          AND centroid_lat IS NOT NULL AND centroid_lng IS NOT NULL
        ORDER BY pincode
    """, (district,))
    return rows


# ---------------------------------------------------------------------------
# Map rendering
# ---------------------------------------------------------------------------

def build_map(pincodes: list[dict]) -> folium.Map:
    """Render all pincode polygons (or centroids) for the district."""
    if not pincodes:
        return folium.Map(location=[20.5, 78.9], zoom_start=5)

    # Center map on the average centroid
    avg_lat = sum(p["centroid_lat"] for p in pincodes) / len(pincodes)
    avg_lng = sum(p["centroid_lng"] for p in pincodes) / len(pincodes)
    fmap = folium.Map(location=[avg_lat, avg_lng], zoom_start=10, tiles="cartodbpositron")

    # Track all coordinates so we can fit-bounds at the end
    bounds: list[tuple[float, float]] = []

    for p in pincodes:
        boundary = p.get("boundary_geojson")
        # boundary_geojson is stored as JSONB; psycopg2 returns it as dict already
        if boundary and isinstance(boundary, str):
            try:
                boundary = json.loads(boundary)
            except (json.JSONDecodeError, TypeError):
                boundary = None

        if boundary and boundary.get("type") == "Polygon":
            # GeoJSON: [lng, lat] pairs; folium wants [lat, lng]
            coords = [[lat, lng] for lng, lat in boundary["coordinates"][0]]
            folium.Polygon(
                locations=coords,
                color="#1e40af", weight=1, fill=True,
                fill_color="#3b82f6", fill_opacity=0.2,
                tooltip=f"{p['pincode']} — {p['city'] or ''}",
            ).add_to(fmap)
            bounds.extend(coords)
        else:
            folium.CircleMarker(
                location=[p["centroid_lat"], p["centroid_lng"]],
                radius=5, color="#1e40af",
                fill=True, fill_color="#3b82f6", fill_opacity=0.6,
                tooltip=f"{p['pincode']} — {p['city'] or ''} (no polygon)",
            ).add_to(fmap)
            bounds.append((p["centroid_lat"], p["centroid_lng"]))

    if bounds:
        fmap.fit_bounds(bounds, padding=(20, 20))
    return fmap


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("Area")

    states = get_states()
    state = st.selectbox(
        "State",
        options=[""] + states,
        format_func=lambda x: "— Choose state —" if x == "" else x,
    )

    if state:
        districts = get_districts(state)
        district = st.selectbox(
            "District",
            options=[""] + districts,
            format_func=lambda x: "— Choose district —" if x == "" else x,
        )
    else:
        district = ""
        st.selectbox("District", options=["— Pick a state first —"], disabled=True)


# ---------------------------------------------------------------------------
# Main content
# ---------------------------------------------------------------------------

if not state:
    st.info("👈 Pick a state to begin.")

elif not district:
    st.info(f"👈 Now pick a district in **{state}**.")

else:
    stats = get_district_stats(state, district)
    st.subheader(f"{district}, {state}")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Pincodes", f"{stats['pincodes']:,}")
    c2.metric("With polygon", f"{stats['pincodes_with_polygon']:,}")
    c3.metric("Sub-districts", f"{stats['sub_districts']:,}")
    c4.metric("Villages", f"{stats['villages']:,}")

    if stats["pincodes"] == 0:
        st.warning("No pincodes found for this district.")
    else:
        st.divider()
        st.subheader("Pincode coverage map")
        with st.spinner("Loading map…"):
            pincodes = get_district_pincodes(district)
            fmap = build_map(pincodes)
            st_folium(fmap, height=600, use_container_width=True, returned_objects=[])
