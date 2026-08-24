"""
tabs/tab_outflow_pacing.py
============================
Tab 2: Cumulative Outflow Pacing -- current crop year's YTD outflow vs. a
3-year historical min/max envelope. Supports selecting multiple
commodities: with exactly one selected, shows that commodity's own
series; with more than one, shows a combined "Total (Selected
Commodities)" series (summed outflow across all selected grains, with
its own historical envelope) rather than cluttering the chart with
several overlapping single-commodity envelopes.

Also includes an Export Summary table (all-commodity YTD / Last Yr. /
3-Yr Avg and both variance percentages, with a TOTAL row).
"""

import streamlit as st

from cgc_engine import CORE_COMMODITIES
from cgc_charts import build_cumulative_pacing_fig, build_pacing_summary_table
from tabs._widgets import commodity_multiselect_with_quick_actions


def render(analytics) -> None:
    selected_commodities = commodity_multiselect_with_quick_actions(
        "Commodity",
        CORE_COMMODITIES,
        default=CORE_COMMODITIES,
        key="pacing_commodities",
        help="Select one commodity to chart its own pacing, or several to see a combined "
             "'Total (Selected Commodities)' series instead of overlapping individual lines.",
    )
    if not selected_commodities:
        st.warning("Select at least one commodity.")
        return

    if len(selected_commodities) == 1:
        chart_label = selected_commodities[0]
        df_pacing = analytics.get_cumulative_pacing(chart_label)
    else:
        chart_label = "Total (Selected Commodities)"
        df_pacing = analytics.get_cumulative_pacing_combined(selected_commodities)

    fig = build_cumulative_pacing_fig(df_pacing, chart_label)
    st.plotly_chart(fig, width="stretch")

    with st.expander("Underlying data"):
        st.dataframe(df_pacing, width="stretch")

    st.markdown("**Export Summary — All Commodities**")
    summary_raw = analytics.get_pacing_summary_table()
    summary_display = build_pacing_summary_table(summary_raw)
    st.dataframe(summary_display, width="stretch", hide_index=True)
