"""
tabs/tab_segment_capacity.py
==============================
Tab 1: Segment Capacity Matrix -- stacked stocks vs. licensed capacity,
selectable by supply-chain segment and commodity subset.
"""

import streamlit as st

from cgc_engine import CORE_COMMODITIES
from cgc_charts import build_commercial_stocks_summary_table, build_stacked_capacity_fig
from tabs._widgets import commodity_multiselect_with_quick_actions

SEGMENT_OPTIONS = {
    "Primary Elevators (By Province)": "primary_province",
    "Process Elevators (East/West)": "process_east_west",
    "Export Terminal Ports": "terminal",
}


def render(analytics) -> None:
    segment_label = st.selectbox(
        "Supply Chain Segment", list(SEGMENT_OPTIONS.keys()), key="segcap_segment",
    )
    segment_type = SEGMENT_OPTIONS[segment_label]

    selected_commodities = commodity_multiselect_with_quick_actions(
        "Commodities to include",
        CORE_COMMODITIES,
        default=CORE_COMMODITIES,
        key="segcap_commodities",
        help="Filtering here reruns the chart (including the capacity line). The chart's "
             "own legend is a static color key -- clicking it no longer hides bars, to "
             "avoid confusion with this control.",
    )
    if not selected_commodities:
        st.warning("Select at least one commodity.")
        return

    df_stocks = analytics.get_segment_capacity_snapshot(segment_type, commodities=selected_commodities)
    fig = build_stacked_capacity_fig(df_stocks, segment_type)
    st.plotly_chart(fig, width="stretch")

    if not df_stocks.empty and df_stocks["capacity_is_commodity_specific"].iloc[0]:
        st.caption(
            "Capacity line reflects effective capacity for the selected commodities only "
            "(parsed from the workbook's Commodity/Industry/Ratios columns)."
        )
    else:
        st.caption(
            "Capacity line reflects TOTAL licensed capacity at these facilities across all "
            "products, not just the selected commodities -- a commodity-specific breakdown "
            "isn't available for this segment."
        )

    # -- Commercial Stocks in Pipeline: all 3 segments at once, independent
    # of the Supply Chain Segment selector above (which only scopes the
    # chart to one segment's node-level breakdown).
    st.markdown("**Commercial Stocks in Pipeline**")
    commercial_summary = analytics.get_commercial_stocks_summary(commodities=selected_commodities)
    commercial_display = build_commercial_stocks_summary_table(commercial_summary)
    st.dataframe(commercial_display, width="stretch", hide_index=True)

    with st.expander("Underlying data"):
        st.dataframe(df_stocks, width="stretch")
