"""
tabs/tab_seasonal_pacing.py
=============================
Export Distribution tab -- cumulative YTD export pacing for every
commodity, shown in plain industry language (e.g. "2.4x Normal Pace
(99th Percentile Anomaly)") rather than a raw Z-score, as a diverging bar
chart. PRIMARY metric is cumulative YTD outflow (Week 1 through the
current week) against its historical YTD-at-the-same-week baseline --
"is this crop year's export program running ahead of or behind normal,"
not "was this one week unusual." Current-week (non-cumulative) volume is
shown as a secondary, aggregate KPI card, not the basis of the chart.

Historical baseline spans 2018-19 through the crop year before the
current one (see cgc_engine.SEASONAL_ANOMALY_BASELINE_START_YEAR),
computed by CGCAnalytics.get_seasonal_pacing_anomaly() when no explicit
lookback_years is passed.
"""

import streamlit as st

from cgc_engine import CORE_COMMODITIES
from cgc_charts import build_seasonal_pacing_fig
from tabs._widgets import commodity_multiselect_with_quick_actions


def render(analytics) -> None:
    st.caption(
        "Compares cumulative YTD export pacing against 2018–present benchmarks to "
        "evaluate crop-year export program momentum and corridor clearance."
    )

    selected_commodities = commodity_multiselect_with_quick_actions(
        "Commodities to include",
        CORE_COMMODITIES,
        default=CORE_COMMODITIES,
        key="pacing_anomaly_commodities",
    )
    if not selected_commodities:
        st.warning("Select at least one commodity.")
        return

    df_anomaly = analytics.get_seasonal_pacing_anomaly()
    filtered = df_anomaly[df_anomaly["grain"].isin(selected_commodities)]

    fig = build_seasonal_pacing_fig(df_anomaly, commodities=selected_commodities)
    st.plotly_chart(fig, width="stretch")

    # Secondary summary KPI -- current-week volume, aggregated across the
    # selected commodities, shown as supporting context rather than a
    # per-commodity breakdown that would compete with the chart above.
    total_current_week = filtered["current_week_ktonnes"].sum(skipna=True)
    st.metric("Current-Week Export Volume — Selected Commodities (Kt)", f"{total_current_week:,.1f}")

    with st.expander("Underlying data"):
        cols = ["grain", "crop_year", "grain_week", "current_cum_ktonnes", "current_week_ktonnes",
                "hist_avg", "hist_std", "anomaly_ktonnes", "z_score", "hist_pool"]
        st.dataframe(filtered[cols], width="stretch")
