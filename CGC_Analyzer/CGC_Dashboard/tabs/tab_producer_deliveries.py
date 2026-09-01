"""
tabs/tab_producer_deliveries.py
=================================
Producer Deliveries tab -- simplified to a single table:

    Cumulative YTD Deliveries (Crop-Year Pace) -- YTD volume vs.
    YTD-at-the-same-week last year / 3-yr average, with a
    "Show YTD Provincial Breakdown" expander directly beneath it.

The Weekly Producer Deliveries table, its provincial-breakdown expander,
and the Grain Marketing Calculator are hidden for now (not deleted from
cgc_reports.py/cgc_charts.py -- their underlying facade methods and chart
formatters are untouched, so restoring them here later is a small,
low-risk UI-only change).

Both use a clean 2-step aggregation:

    Western Canada (Primary) = SK + AB + MB + BC
    Total Producer Deliveries = Western Canada + Process (National)

Producer Car deliveries are deliberately excluded -- CGC's own
Explanatory Notes classify those as UNLICENSED handlings, a distinct
channel outside this scope.

Also includes a cumulative deliveries pacing chart (current year vs.
prior year vs. 3-year average).

Data sources: cgc_engine.deliveries_by_province() (worksheet=='Primary',
metric=='Deliveries', broken out by province) and
cgc_engine.process_deliveries_national() (worksheet=='Process',
metric=='Producer Deliveries', national only -- confirmed to carry no
province breakdown in the source data).
"""

import streamlit as st

from cgc_engine import CORE_COMMODITIES
from cgc_charts import (
    build_producer_deliveries_pacing_fig,
    build_ytd_deliveries_table,
    build_ytd_provincial_table,
)
from tabs._widgets import commodity_multiselect_with_quick_actions


def render(analytics) -> None:
    st.subheader("Producer Deliveries Analysis")

    selected_commodities = commodity_multiselect_with_quick_actions(
        "Commodities to include",
        CORE_COMMODITIES,
        default=CORE_COMMODITIES,
        key="deliveries_commodities",
        help="Filtering here recalculates the table and chart below.",
    )
    if not selected_commodities:
        st.warning("Select at least one commodity.")
        return

    summary_raw = analytics.get_producer_deliveries_summary(commodities=selected_commodities)

    st.markdown("**Cumulative YTD Deliveries (Crop-Year Pace)**")
    ytd_main = build_ytd_deliveries_table(summary_raw)
    st.dataframe(ytd_main, width="stretch", hide_index=True)
    with st.expander("Show YTD Provincial Breakdown"):
        ytd_provincial = build_ytd_provincial_table(summary_raw)
        st.dataframe(ytd_provincial, width="stretch", hide_index=True)

    pacing = analytics.get_producer_deliveries_pacing(commodities=selected_commodities)
    fig = build_producer_deliveries_pacing_fig(pacing)
    st.plotly_chart(fig, width="stretch")

    with st.expander("Underlying data"):
        st.markdown("**Summary table (raw numbers)**")
        st.dataframe(summary_raw, width="stretch")
        st.markdown("**Pacing series**")
        st.dataframe(pacing, width="stretch")
