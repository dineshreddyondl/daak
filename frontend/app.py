"""
daak — Hub Coverage Planner
Step 1: Skeleton app to verify the stack works.
"""

import streamlit as st

st.set_page_config(
    page_title="daak — Hub Coverage Planner",
    page_icon="🚚",
    layout="wide",
)

st.title("🚚 daak")
st.caption("Hub Coverage Planner — Loading…")

st.info(
    "This is the skeleton app. If you can see this in your browser, "
    "Streamlit is working correctly. Next step: connect to Postgres."
)

# Show a small health check so we know the page rendered
with st.expander("Environment check", expanded=False):
    import sys
    st.code(f"Python: {sys.version}")
    st.code(f"Streamlit: {st.__version__}")
