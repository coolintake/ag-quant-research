"""
app.py
=======
CanGrainStats — Streamlit dashboard.

Run with:  streamlit run app.py
(NOT `python app.py` -- Streamlit apps are launched through the `streamlit`
CLI, which handles the web server.)

Each tab's rendering logic lives in its own file under tabs/ -- this file
only handles data loading and the sidebar; deleting or adding a tab means
touching one small file plus two lines here, not hunting through one large
script.
"""

import os
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

# os.path.join(os.path.dirname(__file__), ...) specifically (rather than
# the HERE/pathlib pattern above) for safe path resolution on Streamlit
# Cloud, where the working directory at launch can't be assumed.
LOGO_PATH = os.path.join(os.path.dirname(__file__), "cangrainstat_logo.png")
LOGO_EXISTS = os.path.exists(LOGO_PATH)  # graceful fallback if the file is ever missing/not yet added

st.set_page_config(
    page_title="CanGrainStats",
    page_icon=LOGO_PATH if LOGO_EXISTS else "🌾",
    layout="wide",
)


@st.cache_resource(show_spinner="Loading GSW + capacity data...")
def load_analytics(force_refresh: bool = False) -> CGCAnalytics:
    analytics = CGCAnalytics(gsw_data_dir=str(GSW_DATA_DIR), capacity_xlsb_path=str(CAPACITY_PATH))
    analytics.refresh(force_refresh=force_refresh)
    return analytics


# Header: logo (left) + title/subtitle/callout (right) in a 1:4 column
# split. Title + subtitle render immediately (they don't depend on data).
# The data callout DOES need `analytics` loaded first to know the real
# crop year/week -- rather than delay the whole header until after that
# load finishes, an st.empty() placeholder reserves its exact visual slot
# now, inside the right column, and gets filled in once the real values
# are resolved further down. A placeholder keeps the position it was
# created in even when written to later from outside that `with` block,
# so this still lands in the right column correctly. Rem-based font sizes
# (no fixed text widths) and st.columns' own automatic stacking on narrow
# viewports keep this responsive inside an iframe of any width.
#
# Callout font-size is a fixed 1.2rem per spec (not a ratio of the title
# this time). The h1 explicitly zeroes BOTH margin-top and margin-bottom --
# browsers apply a non-trivial default top margin to <h1> tags that isn't
# obvious until something sits directly beside it (like this logo); leaving
# only margin-bottom set, as the previous version did, is exactly what
# caused the title to sit visibly lower than the top of the logo.
col_logo, col_title = st.columns([1, 4])
with col_logo:
    if LOGO_EXISTS:
        st.image(LOGO_PATH, width=130)
with col_title:
    st.markdown(
        """
        <h1 style="margin-top: 0; margin-bottom: 0.2rem; padding: 0; font-size: 3.2rem; font-weight: 800; line-height: 1.15;">CanGrainStats</h1>
        <p style="margin: 0; font-size: 1.5rem; color: #555555;">
            Interactive market intelligence based on Canadian Grain Commission data.
        </p>
        """,
        unsafe_allow_html=True,
    )
    data_callout_slot = st.empty()

with st.sidebar:
    st.header("Data")
    st.caption(f"Capacity file: `{CAPACITY_PATH}`")
    st.caption(f"Found: {'✅' if CAPACITY_PATH.exists() else '❌ not found'}")
    if st.button("🔄 Refresh data"):
        load_analytics.clear()
        st.rerun()
    st.caption("Data is cached after first load. Use Refresh to re-download the current crop year.")

    st.sidebar.markdown("---")
    st.sidebar.caption(
        "Disclaimer: CanGrainStats is an independent market intelligence tool built for "
        "informational and educational purposes only. Data is sourced from public Canadian "
        "Grain Commission (CGC) reports. This platform does not constitute trading, "
        "financial, or commercial advice. Users assume all responsibility for trading "
        "decisions or analysis derived from this platform."
    )

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
data_callout_slot.markdown(
    f'<p style="margin: 0.4rem 0 1rem 0; font-size: 1.2rem; font-weight: 600; color: #1a1a1a;">'
    f'Latest data available: Crop Year {latest_crop_year} | Grain Week {latest_grain_week}'
    f'</p>',
    unsafe_allow_html=True,
)

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "Commercial Pipeline", "Export Pace", "Capacity Bottleneck",
    "Export Distribution", "Producer Deliveries",
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
