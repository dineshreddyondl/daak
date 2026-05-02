"""
daak — Hub Coverage Planner
Step 3: District selector + per-district stats.
"""

import streamlit as st
from db import fetch_all

st.set_page_config(
    page_title="daak — Hub Coverage Planner",
    page_icon="🚚",
    layout="wide",
)

st.title("🚚 daak")
st.caption("Hub Coverage Planner")


# ---------------------------------------------------------------------------
# Data access (cached for performance)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300)
def get_states() -> list[str]:
    """States available in the hierarchy table."""
    rows = fetch_all("""
        SELECT DISTINCT state
        FROM geographic_hierarchy
        WHERE state IS NOT NULL AND state != ''
        ORDER BY state
    """)
    return [r["state"] for r in rows]


@st.cache_data(ttl=300)
def get_districts(state: str) -> list[str]:
    rows = fetch_all("""
        SELECT DISTINCT district
        FROM geographic_hierarchy
        WHERE state = %s AND district IS NOT NULL AND district != ''
        ORDER BY district
    """, (state,))
    return [r["district"] for r in rows]


@st.cache_data(ttl=60)
def get_district_stats(state: str, district: str) -> dict:
    """Counts for a given district."""
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


# ---------------------------------------------------------------------------
# Sidebar — area selection
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
        st.warning(
            f"No pincodes found for '{district}'. "
            "This might be a name mismatch — district exists in hierarchy but not in pincodes_master."
        )

    st.divider()
    st.caption("Next step: place virtual hubs and compute coverage.")
