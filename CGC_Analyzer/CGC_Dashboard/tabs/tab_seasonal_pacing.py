"""
tabs/tab_seasonal_pacing.py
=============================
Tab: Seasonal Pacing Anomaly -- current-week outflow pace for every
commodity, shown in plain industry language (e.g. "2.4x Normal Pace
(99th Percentile Anomaly)") rather than a raw Z-score, as a diverging bar
chart. Complements the Cumulative Outflow Pacing tab (which shows one
commodity's full-season trajectory in detail) by showing every
commodity's current deviation at a glance instead.

A plain-English interpretation banner appears below the chart whenever
any selected commodity has crossed the "extreme" threshold
(cgc_charts.PACING_EXTREME_THRESHOLD, |Z| >= 2), calling out the likely
physical pipeline implication (terminal congestion risk if running fast,
on-farm storage pressure if running slow) rather than leaving the reader
to infer it from a bar chart alone.
"""

import streamlit as st

from cgc_engine import CORE_COMMODITIES
from cgc_charts import PACING_EXTREME_THRESHOLD, build_seasonal_pacing_fig
from tabs._widgets import commodity_multiselect_with_quick_actions


def render(analytics) -> None:
    st.caption(
        "How each commodity's current-week outflow compares to its 3-year historical "
        "average for the same grain week -- shown as a simple multiple of normal pace "
        "and an approximate percentile, rather than a raw statistical score. Right of "
        "center = running faster than history; left = running slower. Bars sorted by "
        "value, not commodity name, so the extremes sit at each end."
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

    extreme = df_anomaly[
        df_anomaly["grain"].isin(selected_commodities)
        & df_anomaly["z_score"].notna()
        & (df_anomaly["z_score"].abs() >= PACING_EXTREME_THRESHOLD)
    ]
    if not extreme.empty:
        lines = []
        for _, row in extreme.iterrows():
            if row["z_score"] > 0:
                lines.append(
                    f"**{row['grain']}** is moving significantly faster than its "
                    f"historical norm -- this can signal strong demand pulling deliveries "
                    f"out faster than the system typically handles, with a risk of "
                    f"downstream **terminal congestion** if the pace continues."
                )
            else:
                lines.append(
                    f"**{row['grain']}** is moving significantly slower than its "
                    f"historical norm -- this can signal soft demand or a logistics "
                    f"bottleneck, with a risk of **on-farm storage pressure** building if "
                    f"the pace doesn't recover."
                )
        st.warning("  \n\n".join(lines))

    with st.expander("Underlying data"):
        cols = ["grain", "crop_year", "grain_week", "current_outflow_ktonnes",
                "hist_avg", "hist_std", "anomaly_ktonnes", "z_score", "hist_pool"]
        filtered = df_anomaly[df_anomaly["grain"].isin(selected_commodities)][cols]
        st.dataframe(filtered, width="stretch")
