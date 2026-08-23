"""
tabs/tab_outflow_pacing.py
============================
Tab 2: Cumulative Outflow Pacing -- current crop year's YTD outflow vs. a
3-year historical min/max envelope, one commodity at a time, plus an
all-commodity summary table beneath it (YTD / Last Yr. / 3-Yr Avg and both
variance percentages, with a TOTAL row).
"""

import streamlit as st

from cgc_engine import CORE_COMMODITIES
from cgc_charts import build_cumulative_pacing_fig, build_pacing_summary_table


def render(analytics) -> None:
    commodity = st.selectbox("Commodity", CORE_COMMODITIES, key="pacing_commodity")

    df_pacing = analytics.get_cumulative_pacing(commodity)
    fig = build_cumulative_pacing_fig(df_pacing, commodity)
    st.plotly_chart(fig, width="stretch")

    with st.expander("Underlying data"):
        st.dataframe(df_pacing, width="stretch")

    st.markdown("**Pacing Summary — All Commodities (kMT)**")
    summary_raw = analytics.get_pacing_summary_table()
    summary_display = build_pacing_summary_table(summary_raw)
    st.dataframe(summary_display, width="stretch", hide_index=True)
