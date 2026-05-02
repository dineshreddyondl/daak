"""
daak — Hub Coverage Planner
Step 2: Connect to Postgres, show database stats.
"""

import streamlit as st
from db import fetch_all

st.set_page_config(
    page_title="daak — Hub Coverage Planner",
    page_icon="🚚",
    layout="wide",
)

st.title("🚚 daak")
st.caption("Hub Coverage Planner — Step 2: Database connected")


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@st.cache_data(ttl=60)
def get_db_summary() -> dict:
    """Top-line counts to verify the data layer is wired up."""
    rows = fetch_all("""
        SELECT
            (SELECT COUNT(*) FROM pincodes_master)                  AS total_pincodes,
            (SELECT COUNT(*) FROM pincodes_master WHERE has_polygon) AS pincodes_with_polygon,
            (SELECT COUNT(DISTINCT district) FROM pincodes_master)  AS total_districts_in_pincodes,
            (SELECT COUNT(DISTINCT state)    FROM pincodes_master)  AS total_states_in_pincodes,
            (SELECT COUNT(*) FROM geographic_hierarchy)             AS total_villages,
            (SELECT COUNT(DISTINCT district) FROM geographic_hierarchy) AS total_districts_in_hierarchy,
            (SELECT COUNT(DISTINCT state)    FROM geographic_hierarchy) AS total_states_in_hierarchy
    """)
    return rows[0]


@st.cache_data(ttl=60)
def get_pincodes_per_state() -> list:
    return fetch_all("""
        SELECT state, COUNT(*) AS pincodes
        FROM pincodes_master
        WHERE state IS NOT NULL AND state != ''
        GROUP BY state
        ORDER BY pincodes DESC
        LIMIT 15
    """)


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

try:
    summary = get_db_summary()
    st.success("✅ Database connected")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Pincodes", f"{summary['total_pincodes']:,}")
    col2.metric("With polygon", f"{summary['pincodes_with_polygon']:,}")
    col3.metric("Villages", f"{summary['total_villages']:,}")
    col4.metric("States (hierarchy)", summary['total_states_in_hierarchy'])

    st.divider()

    st.subheader("Pincodes per state (top 15)")
    rows = get_pincodes_per_state()
    st.dataframe(rows, use_container_width=True, hide_index=True)

except Exception as e:
    st.error(f"❌ Database connection failed: {e}")
    st.code(
        "Check that:\n"
        "1. Docker container 'daak-pg' is running (`docker ps`)\n"
        "2. .env file exists in frontend/ with DATABASE_URL\n"
        "3. Schema and data have been loaded"
    )
