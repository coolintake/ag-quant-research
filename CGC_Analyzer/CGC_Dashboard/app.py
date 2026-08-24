"""
app.py
=======
CGC Grain Analytics — Streamlit dashboard.

Run with:  streamlit run app.py
(NOT `python app.py` -- Streamlit apps are launched through the `streamlit`
CLI, which handles the web server.)

Each tab's rendering logic lives in its own file under tabs/ -- this file
only handles data loading and the sidebar; deleting or adding a tab means
touching one small file plus two lines here, not hunting through one large
script.
"""

from pathlib import Path
import streamlit as st

from cgc_reports import CGCAnalytics
from tabs import tab_bottleneck_matrix, tab_outflow_pacing, tab_producer_deliveries, tab_seasonal_pacing, tab_segment_capacity

# Self-locating paths: these always resolve relative to THIS file's folder,
# regardless of where `streamlit run` is launched from. Put
# CGC_Capacity.xlsb directly in this same folder and it'll be found
# automatically -- no path editing required.
HERE = Path(__file__).resolve().parent
GSW_DATA_DIR = HERE / "gsw_data"
CAPACITY_PATH = HERE / "CGC_Capacity.xlsb"

st.set_page_config(page_title="CGC Grain Analytics", layout="wide")


@st.cache_resource(show_spinner="Loading GSW + capacity data...")
def load_analytics(force_refresh: bool = False) -> CGCAnalytics:
    analytics = CGCAnalytics(gsw_data_dir=str(GSW_DATA_DIR), capacity_xlsb_path=str(CAPACITY_PATH))
    analytics.refresh(force_refresh=force_refresh)
    return analytics


st.title("CGC Grain Market Analytics")

with st.sidebar:
    st.header("Data")
    st.caption(f"Capacity file: `{CAPACITY_PATH}`")
    st.caption(f"Found: {'✅' if CAPACITY_PATH.exists() else '❌ not found'}")
    if st.button("🔄 Refresh data"):
        load_analytics.clear()
        st.rerun()
    st.caption("Data is cached after first load. Use Refresh to re-download the current crop year.")

if not CAPACITY_PATH.exists():
    st.error(
        f"CGC_Capacity.xlsb was not found at:\n\n`{CAPACITY_PATH}`\n\n"
        f"Copy/move your CGC_Capacity.xlsb file into this folder:\n\n`{HERE}`"
    )
    st.stop()

try:
    analytics = load_analytics()
except Exception as exc:
    st.error(f"Failed to load data: {exc}")
    st.stop()

latest_crop_year = analytics._resolve_crop_year(None)
latest_grain_week = analytics._resolve_grain_week(latest_crop_year, None)
st.caption(f"Latest data available: crop year **{latest_crop_year}**, grain week **{latest_grain_week}**")

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "Segment Capacity Matrix", "Cumulative Outflow Pacing", "Bottleneck Matrix",
    "Seasonal Pacing Anomaly", "Producer Deliveries",
])

with tab1:
    tab_segment_capacity.render(analytics)

with tab2:
    tab_outflow_pacing.render(analytics)

with tab3:
    tab_bottleneck_matrix.render(analytics)

with tab4:
    tab_seasonal_pacing.render(analytics)

with tab5:
    tab_producer_deliveries.render(analytics)
