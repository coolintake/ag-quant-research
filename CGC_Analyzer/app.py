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

import base64
import io
import os
from pathlib import Path
from typing import Optional
import streamlit as st

from cgc_reports import CGCAnalytics
from tabs import tab_bottleneck_matrix, tab_outflow_pacing, tab_producer_deliveries, tab_seasonal_pacing, tab_segment_capacity

# Self-locating paths: these always resolve relative to THIS file's folder,
# regardless of where `streamlit run` is launched from. Put your capacity
# workbook directly in this same folder and it'll be found automatically
# -- no path editing required.
HERE = Path(__file__).resolve().parent
GSW_DATA_DIR = HERE / "gsw_data"


def _resolve_capacity_path(here: Path) -> Path:
    """Find CGC_Capacity.(xlsb|xlsx|xls) in `here`, preferring .xlsb (the
    workbook's native licensed format) if more than one is present, but
    accepting whichever extension actually exists -- CapacityLoader
    already reads all three natively, so the app shouldn't hard-fail just
    because the file was saved/exported as .xlsx instead of .xlsb.
    Falls back to the .xlsb path (even if it doesn't exist yet) so the
    "not found" error message below still shows a sensible expected name.
    """
    for ext in (".xlsb", ".xlsx", ".xls"):
        candidate = here / f"CGC_Capacity{ext}"
        if candidate.exists():
            return candidate
    return here / "CGC_Capacity.xlsb"


CAPACITY_PATH = _resolve_capacity_path(HERE)

# os.path.join(os.path.dirname(__file__), ...) specifically (rather than
# the HERE/pathlib pattern above) for safe path resolution on Streamlit
# Cloud, where the working directory at launch can't be assumed.
LOGO_PATH = os.path.join(os.path.dirname(__file__), "cangrainstat_logo.png")
LOGO_EXISTS = os.path.exists(LOGO_PATH)  # graceful fallback if the file is ever missing/not yet added


def _encode_logo_base64(path: str) -> Optional[str]:
    """Read the logo file and return its base64-encoded string, for
    embedding directly in the header's inline HTML via a data: URI --
    avoids depending on Streamlit's own static-file serving, which can
    behave differently across local dev vs. Streamlit Cloud deployments.
    Returns None (rather than raising) if the file doesn't exist, so a
    missing logo degrades gracefully instead of crashing the whole app.
    """
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        buffer = io.BytesIO(f.read())
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


BASE64_LOGO = _encode_logo_base64(LOGO_PATH)

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
        f"CGC_Capacity workbook was not found at:\n\n`{CAPACITY_PATH}`\n\n"
        f"Copy/move your CGC_Capacity.xlsb, .xlsx, or .xls file into this folder:\n\n`{HERE}`"
    )
    st.stop()

try:
    analytics = load_analytics()
except Exception as exc:
    st.error(f"Failed to load data: {exc}")
    st.stop()

latest_crop_year = analytics._resolve_crop_year(None)
latest_grain_week = analytics._resolve_grain_week(latest_crop_year, None)

# Header: a single inline flexbox block (logo + title + subtitle + data
# callout all together), per spec -- this is deliberately NOT split across
# st.columns or an st.empty() placeholder, so it needs latest_crop_year/
# latest_grain_week already resolved before it can render. That's why this
# whole block now sits AFTER data loading, rather than appearing instantly
# at the top of the page as in the previous column-based version: the
# title and logo won't show at all until analytics has loaded
# successfully, including during the error states above (missing capacity
# file, failed load) -- a real trade-off of the single-block approach,
# not an oversight.
logo_html = (
    f'<img src="data:image/png;base64,{BASE64_LOGO}" style="width: 80px; height: auto;">'
    if BASE64_LOGO else ""
)
# Built as ONE continuous string with NO embedded newlines/indentation --
# a multi-line triple-quoted string here (even with unsafe_allow_html=True)
# risks Streamlit's Markdown parser treating the indented lines as a
# literal code block instead of raw HTML, which shows the tags themselves
# as visible text on the page rather than rendering them. Adjacent string
# literals inside parentheses concatenate automatically in Python, so the
# source below stays readable while the actual string handed to
# st.markdown() has zero linebreaks for Markdown to misinterpret.
header_html = (
    '<div style="display: flex; align-items: center; gap: 15px; margin-bottom: 10px;">'
    + logo_html +
    '<div>'
    '<h1 style="margin: 0; font-size: 3.2rem; font-weight: 800; line-height: 1.1;">CanGrainStats</h1>'
    '<p style="margin: 5px 0 0 0; font-size: 1.3rem; color: #444;">'
    'Interactive market intelligence based on Canadian Grain Commission data.'
    '</p>'
    f'<p style="margin: 3px 0 0 0; font-size: 1.1rem; font-weight: 600; color: #666;">'
    f'Latest data available: Crop Year {latest_crop_year} | Grain Week {latest_grain_week}'
    '</p>'
    '</div>'
    '</div>'
)
st.markdown(header_html, unsafe_allow_html=True)

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
