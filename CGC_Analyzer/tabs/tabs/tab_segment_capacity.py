"""
tabs/tab_segment_capacity.py
==============================
Commercial Pipeline tab -- a horizontal pill selector picks between 4
mutually exclusive views: the aggregate "TOTAL COMMERCIAL STOCKS" summary
(all 3 elevator segments at once) or one specific segment's stacked
stocks-vs-capacity chart (Primary / Process / Terminal).
"""

import streamlit as st

from cgc_engine import CORE_COMMODITIES
from cgc_charts import build_commercial_stocks_summary_table, build_stacked_capacity_fig
from tabs._widgets import commodity_multiselect_with_quick_actions, pills_single_select_with_reset

SEGMENT_OPTIONS = {
    "Primary": "primary_province",
    "Process": "process_east_west",
    "Terminal": "terminal",
}
PIPELINE_SEGMENT_CHOICES = ["TOTAL COMMERCIAL STOCKS", "Primary", "Process", "Terminal"]


def render(analytics) -> None:
    pipeline_segment = pills_single_select_with_reset(
        "Pipeline Segment",
        PIPELINE_SEGMENT_CHOICES,
        default="TOTAL COMMERCIAL STOCKS",
        key="segcap_pipeline_segment",
    )

    selected_commodities = commodity_multiselect_with_quick_actions(
        "Commodities to include",
        CORE_COMMODITIES,
        default=CORE_COMMODITIES,
        key="segcap_commodities",
        help="Filtering here reruns the chart or table below (including the capacity line, "
             "where applicable). A chart's own legend is a static color key -- clicking it "
             "no longer hides bars, to avoid confusion with this control.",
    )
    if not selected_commodities:
        st.warning("Select at least one commodity.")
        return

    if pipeline_segment == "TOTAL COMMERCIAL STOCKS":
        st.markdown("**Commercial Stocks in Pipeline**")
        commercial_summary = analytics.get_commercial_stocks_summary(commodities=selected_commodities)
        commercial_display = build_commercial_stocks_summary_table(commercial_summary)
        st.dataframe(commercial_display, width="stretch", hide_index=True)

        with st.expander("Underlying data"):
            st.dataframe(commercial_summary, width="stretch")
        return

    segment_type = SEGMENT_OPTIONS[pipeline_segment]
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

    with st.expander("Underlying data"):
        st.dataframe(df_stocks, width="stretch")
