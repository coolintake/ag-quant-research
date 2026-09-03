"""
tabs/tab_bottleneck_matrix.py
===============================
Tab 3: Bottleneck Matrix -- a single system-wide heatmap covering every
segment and commodity at once. National Primary Elevators, Process
Elevators, and the 6 terminal ports come from
CGCAnalytics.get_regional_utilization_matrix() directly (no new engine
work: same bottleneck_tag thresholds computed and tested elsewhere in the
app). The Saskatchewan/Alberta/Manitoba rows nested beneath Primary
Elevators reuse get_segment_capacity_snapshot('primary_province') --
already built and tested for Tab 1 -- rather than requiring new engine
work of their own.
"""

import streamlit as st

from cgc_engine import CORE_COMMODITIES
from cgc_charts import build_bottleneck_matrix_fig
from tabs._widgets import commodity_multiselect_with_quick_actions


def render(analytics) -> None:
    st.caption(
        "System-wide utilization snapshot across every segment and commodity, for the "
        "latest available crop year and grain week. National Primary Elevators, Process "
        "Elevators, and each terminal port use the same Red/Yellow/Green thresholds as "
        "the rest of the app; the indented rows beneath Primary Elevators break it down "
        "by province."
    )

    selected_commodities = commodity_multiselect_with_quick_actions(
        "Commodities to include",
        CORE_COMMODITIES,
        default=CORE_COMMODITIES,
        key="bottleneck_commodities",
    )
    if not selected_commodities:
        st.warning("Select at least one commodity.")
        return

    df_matrix = analytics.get_regional_utilization_matrix()
    df_province = analytics.get_segment_capacity_snapshot("primary_province", commodities=selected_commodities)
    fig = build_bottleneck_matrix_fig(df_matrix, df_province, commodities=selected_commodities)
    st.plotly_chart(fig, width="stretch")

    with st.expander("Underlying data"):
        st.markdown("**National (Primary Elevators / Process Elevators / terminals)**")
        cols = ["grain", "segment", "corridor", "stocks_ktonnes", "capacity_ktonnes",
                "capacity_utilization_pct", "velocity_context", "bottleneck_tag"]
        st.dataframe(df_matrix[df_matrix["grain"].isin(selected_commodities)][cols], width="stretch")

        st.markdown("**Provincial Primary Elevator breakdown (Saskatchewan / Alberta / Manitoba)**")
        province_cols = ["node"] + selected_commodities + ["capacity_ktonnes", "total_stocks_ktonnes", "pct_utilized"]
        province_cols = [c for c in province_cols if c in df_province.columns]
        st.dataframe(
            df_province[df_province["node"].isin(["SK", "AB", "MB"])][province_cols],
            width="stretch",
        )
