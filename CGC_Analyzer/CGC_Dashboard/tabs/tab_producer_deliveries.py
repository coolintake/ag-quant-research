"""
tabs/tab_producer_deliveries.py
=================================
Tab: Producer Deliveries -- two frequency-based tables shown side by side:

    Table 1 (left):  Weekly Producer Deliveries (Current Week) -- this
                      week's volume vs. the SAME WEEK last year / 3-yr avg.
    Table 2 (right): Cumulative YTD Deliveries (Crop-Year Pace) -- YTD
                      volume vs. YTD-at-the-same-week last year / 3-yr avg.

Both use a clean 2-step aggregation:

    Western Canada (Primary) = SK + AB + MB + BC
    Total Producer Deliveries = Western Canada + Process (National)

Producer Car deliveries are deliberately excluded -- CGC's own
Explanatory Notes classify those as UNLICENSED handlings, a distinct
channel outside this scope. Each table shows the 3 main streams up
front, with its own SK/AB/MB/BC provincial-breakdown expander directly
beneath it (in the same column) to keep the primary view uncluttered.

Also includes the Grain Marketing Calculator (To Be Sold) -- evaluated
against Table 2's Total Producer Deliveries, showing both Marketed % and
Remaining On-Farm Supply -- and a cumulative deliveries pacing chart
(current year vs. prior year vs. 3-year average).

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
    build_weekly_deliveries_table,
    build_weekly_provincial_table,
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
        help="Filtering here recalculates both tables, the calculator, and the chart below.",
    )
    if not selected_commodities:
        st.warning("Select at least one commodity.")
        return

    summary_raw = analytics.get_producer_deliveries_summary(commodities=selected_commodities)

    # -- A/B. Table 1 (Weekly) and Table 2 (YTD), side by side --
    col_weekly, col_ytd = st.columns(2)

    with col_weekly:
        st.markdown("**Weekly Producer Deliveries (Current Week)**")
        weekly_main = build_weekly_deliveries_table(summary_raw)
        st.dataframe(weekly_main, width="stretch", hide_index=True)
        with st.expander("Show Weekly Provincial Breakdown"):
            weekly_provincial = build_weekly_provincial_table(summary_raw)
            st.dataframe(weekly_provincial, width="stretch", hide_index=True)

    with col_ytd:
        st.markdown("**Cumulative YTD Deliveries (Crop-Year Pace)**")
        ytd_main = build_ytd_deliveries_table(summary_raw)
        st.dataframe(ytd_main, width="stretch", hide_index=True)
        with st.expander("Show YTD Provincial Breakdown"):
            ytd_provincial = build_ytd_provincial_table(summary_raw)
            st.dataframe(ytd_provincial, width="stretch", hide_index=True)

    # -- C. Grain Marketing Calculator (To Be Sold) --
    with st.expander("📊 Grain Marketing Calculator (To Be Sold)", expanded=True):
        selected_row = summary_raw[summary_raw["region"] == "Total Producer Deliveries"].iloc[0]
        selected_ytd = selected_row["ytd_ktonnes"]
        selected_ytd = 0.0 if selected_ytd != selected_ytd else selected_ytd  # NaN-safe

        st.metric("Selected YTD Deliveries (KMT)", f"{selected_ytd:,.0f}")

        col1, col2 = st.columns(2)
        with col1:
            production_kmt = st.number_input(
                "StatsCan Production (KMT)", min_value=0.0, value=0.0, step=100.0,
                key="deliveries_calc_production",
            )
        with col2:
            carry_in_kmt = st.number_input(
                "On-Farm Carry-In (KMT)", min_value=0.0, value=0.0, step=100.0,
                key="deliveries_calc_carry_in",
            )

        denominator = production_kmt + carry_in_kmt
        pace_pct = (selected_ytd / denominator * 100.0) if denominator > 0 else 0.0
        remaining_kmt = denominator - selected_ytd

        col_a, col_b = st.columns(2)
        with col_a:
            st.metric("Marketed %", f"{pace_pct:.1f}%")
        with col_b:
            st.metric("Remaining On-Farm Supply (KMT)", f"{remaining_kmt:,.0f}")

    # -- D. Cumulative Producer Deliveries Pacing chart --
    pacing = analytics.get_producer_deliveries_pacing(commodities=selected_commodities)
    fig = build_producer_deliveries_pacing_fig(pacing)
    st.plotly_chart(fig, width="stretch")

    with st.expander("Underlying data"):
        st.markdown("**Summary table (raw numbers)**")
        st.dataframe(summary_raw, width="stretch")
        st.markdown("**Pacing series**")
        st.dataframe(pacing, width="stretch")
