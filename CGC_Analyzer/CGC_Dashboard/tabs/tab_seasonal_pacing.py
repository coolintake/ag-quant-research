"""
tabs/tab_seasonal_pacing.py
=============================
Tab: Seasonal Pacing Anomaly -- current-week outflow pace for every
commodity, expressed as a Z-score against its historical average, as a
diverging bar chart. Complements the Cumulative Outflow Pacing tab (which
shows one commodity's full-season trajectory in detail) by showing every
commodity's current deviation at a glance instead.
"""

import streamlit as st

from cgc_engine import CORE_COMMODITIES
from cgc_charts import build_seasonal_pacing_fig
from tabs._widgets import commodity_multiselect_with_quick_actions


def render(analytics) -> None:
    st.caption(
        "How each commodity's current-week outflow compares to its 3-year historical "
        "average for the same grain week, as a Z-score (standard deviations from the "
        "mean). Positive = running faster than history; negative = running slower. "
        "Bars sorted by value, not commodity name, so the extremes sit at each end."
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
    fig = build_seasonal_pacing_fig(df_anomaly, commodities=selected_commodities)
    st.plotly_chart(fig, width="stretch")

    with st.expander("Underlying data"):
        cols = ["grain", "crop_year", "grain_week", "current_outflow_ktonnes",
                "hist_avg", "hist_std", "anomaly_ktonnes", "z_score", "hist_pool"]
        filtered = df_anomaly[df_anomaly["grain"].isin(selected_commodities)][cols]
        st.dataframe(filtered, width="stretch")
