"""
cgc_charts.py
==============
All Plotly chart-building functions for the dashboard, kept separate from
`cgc_reports.py` (which owns data assembly, not rendering) so a chart bug
and a data bug can never hide in the same file.

    build_stacked_capacity_fig(df_stocks, segment_type)   -- Tab 1
    build_cumulative_pacing_fig(df_pacing, commodity)      -- Tab 2
    build_bottleneck_matrix_fig(df_matrix, commodities)    -- Tab 3

Legend-click fix: `build_stacked_capacity_fig`'s legend previously allowed
clicking a commodity swatch to hide/show that bar segment client-side in
Plotly, with zero effect on the capacity line or utilization numbers --
easy to mistake for the real "Commodities to include" multiselect above
the chart, which *does* recompute everything. The legend is now a
non-interactive color key (`legend_itemclick=False`,
`legend_itemdoubleclick=False`); all filtering goes through the
multiselect instead.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from cgc_engine import CORE_COMMODITIES, COMMODITY_COLORS, TERMINAL_LOCATIONS, to_display_grain_name

SEGMENT_TITLES: Dict[str, str] = {
    "primary_province": "Primary Elevators — By Province",
    "process_east_west": "Process Elevators — East/West",
    "terminal": "Export Terminal Ports",
}


# ═══════════════════════════════════════════════════════════════════════════
# TAB 1 — Segment Capacity Matrix
# ═══════════════════════════════════════════════════════════════════════════

def build_stacked_capacity_fig(df_stocks: pd.DataFrame, segment_type: str) -> go.Figure:
    """Horizontal stacked bar of stocks per node (one bar segment per
    commodity actually present in `df_stocks`), with a dashed red vertical
    line marking licensed capacity and a direct text label
    ('% Utilized (Total / Capacity Kt)') to the right of each row.

    Parameters
    ----------
    df_stocks : pd.DataFrame
        Output of `CGCAnalytics.get_segment_capacity_snapshot`: one row per
        node, one column per selected commodity, plus capacity_ktonnes /
        total_stocks_ktonnes / pct_utilized / capacity_is_commodity_specific.
        Only commodities present as columns get a bar segment -- pass a
        commodity-filtered snapshot to show fewer than all 6.
    segment_type : str
        'primary_province' | 'process_east_west' | 'terminal' -- used for
        the chart title.
    """
    df = df_stocks.reset_index(drop=True)
    y_pos = list(range(len(df)))
    nodes = df["node"].tolist()
    present_commodities = [c for c in CORE_COMMODITIES if c in df.columns]
    capacity_is_commodity_specific = bool(df["capacity_is_commodity_specific"].iloc[0]) \
        if "capacity_is_commodity_specific" in df.columns and not df.empty else False

    fig = go.Figure()
    for commodity in present_commodities:
        fig.add_trace(go.Bar(
            name=commodity,
            x=df[commodity],
            y=y_pos,
            orientation="h",
            marker_color=COMMODITY_COLORS[commodity],
        ))

    for i, row in df.iterrows():
        cap = row.get("capacity_ktonnes", np.nan)
        total = row.get("total_stocks_ktonnes", 0.0)
        pct = row.get("pct_utilized", np.nan)
        is_reported = bool(row.get("is_reported", True))

        if pd.notna(cap) and cap > 0:
            fig.add_shape(
                type="line", xref="x", yref="y",
                x0=cap, x1=cap, y0=i - 0.4, y1=i + 0.4,
                line=dict(color="red", dash="dash", width=2),
            )

        if not is_reported:
            label = f"Not reported by GSW (province-level stocks unavailable; capacity {cap:,.0f} Kt)"
        elif pd.notna(pct):
            label = f"{pct:.0f}% Utilized  ({total:,.0f} / {cap:,.0f} Kt)"
        else:
            label = "n/a"
        fig.add_annotation(
            xref="paper", x=1.02, yref="y", y=i,
            text=label,
            showarrow=False, xanchor="left", align="left",
            font=dict(size=11, color="#888888" if not is_reported else "#000000"),
        )

    max_x = float(max(df["capacity_ktonnes"].max() if "capacity_ktonnes" in df else 0,
                       df["total_stocks_ktonnes"].max() if "total_stocks_ktonnes" in df else 0, 1))

    commodity_note = (
        "capacity line = effective capacity for the selected commodities only"
        if capacity_is_commodity_specific
        else "capacity line = TOTAL licensed capacity across all products at these facilities (not commodity-specific)"
    )
    fig.update_layout(
        barmode="stack",
        title=dict(
            text=f"Stocks vs. Licensed Capacity — {SEGMENT_TITLES.get(segment_type, segment_type)}"
                 f"<br><sup>{commodity_note}</sup>",
        ),
        xaxis=dict(title="Ktonnes", range=[0, max_x * 1.08]),
        yaxis=dict(tickmode="array", tickvals=y_pos, ticktext=nodes, autorange="reversed"),
        legend=dict(
            title="Commodity", orientation="h", yanchor="top", y=-0.15, xanchor="center", x=0.5,
            itemclick=False, itemdoubleclick=False,  # static color key -- filtering happens via the multiselect above
        ),
        template="plotly_white",
        height=max(320, 90 + 60 * len(df)),
        margin=dict(r=260, l=140, t=75, b=90),
    )
    return fig


# ═══════════════════════════════════════════════════════════════════════════
# TAB 2 — Cumulative Outflow Pacing
# ═══════════════════════════════════════════════════════════════════════════

def build_cumulative_pacing_fig(df_pacing: pd.DataFrame, selected_commodity: str) -> go.Figure:
    """Cumulative YTD outflow (MMT) line for the current crop year against
    a shaded 3-year historical min/max envelope and a dashed 3-year
    average line, across grain weeks 1-52.

    Parameters
    ----------
    df_pacing : pd.DataFrame
        Output of `CGCAnalytics.get_cumulative_pacing`.
    selected_commodity : str
        Used for the chart title.
    """
    fig = go.Figure()

    if df_pacing.empty:
        fig.update_layout(
            title=f"Cumulative Outflow Pacing — {selected_commodity} (no data available)",
            xaxis=dict(title="Grain Week", range=[1, 52]),
            yaxis=dict(title="Cumulative Outflow (MMT)"),
            template="plotly_white",
        )
        return fig

    weeks = df_pacing["grain_week"]

    fig.add_trace(go.Scatter(
        x=weeks, y=df_pacing["hist_max_mmt"], mode="lines",
        line=dict(width=0), showlegend=False, hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=weeks, y=df_pacing["hist_min_mmt"], mode="lines",
        line=dict(width=0), fill="tonexty", fillcolor="rgba(180, 180, 180, 0.3)",
        name="3-Yr Historical Range", hoverinfo="skip",
    ))

    fig.add_trace(go.Scatter(
        x=weeks, y=df_pacing["hist_avg_mmt"], mode="lines",
        line=dict(color="gray", dash="dash", width=2), name="3-Yr Average Pace",
    ))

    crop_year_label = df_pacing["crop_year"].iloc[0]
    fig.add_trace(go.Scatter(
        x=weeks, y=df_pacing["current_cum_mmt"], mode="lines+markers",
        line=dict(color="#003366", width=3), marker=dict(size=5),
        name=f"{crop_year_label} YTD (Current)",
    ))

    fig.update_layout(
        title=f"Cumulative Outflow Pacing — {selected_commodity}",
        xaxis=dict(title="Grain Week", range=[1, 52], dtick=4),
        yaxis=dict(title="Cumulative Outflow (MMT)"),
        template="plotly_white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
    )
    return fig


# ═══════════════════════════════════════════════════════════════════════════
# TAB 3 — Bottleneck Matrix
# ═══════════════════════════════════════════════════════════════════════════

# Fixed row order: national Primary/Process, then the 6 terminal ports.
# Matches exactly what get_regional_utilization_matrix() already returns --
# no province-level Primary split here (that data doesn't carry velocity/
# weeks-of-supply/bottleneck_tag; see the scope discussion before this tab
# was built). Zero new engine work.
BOTTLENECK_SEGMENT_ORDER: List[str] = ["Primary Elevators", "Process Elevators", *TERMINAL_LOCATIONS]

# Same Red/Yellow/Green hex values used elsewhere in the app (the stacked
# capacity chart's own threshold coloring), so a Red cell here and a Red
# label there always mean the same color. N/A gray is new to this chart.
_BOTTLENECK_TAG_COLORS: Dict[str, str] = {
    "N/A": "#CCCCCC", "Green": "#2ca02c", "Yellow": "#e6b800", "Red": "#d62728",
}
_BOTTLENECK_TAG_ORDER: List[str] = ["N/A", "Green", "Yellow", "Red"]
_BOTTLENECK_TAG_Z: Dict[str, int] = {tag: i for i, tag in enumerate(_BOTTLENECK_TAG_ORDER)}


def _discrete_colorscale(tags_in_order: List[str], color_map: Dict[str, str]) -> list:
    """Build a Plotly colorscale with hard (non-interpolated) bands, one
    per category in `tags_in_order`, each occupying an equal-width slice.
    """
    n = len(tags_in_order)
    colorscale = []
    for i, tag in enumerate(tags_in_order):
        color = color_map[tag]
        colorscale.append([i / n, color])
        colorscale.append([(i + 1) / n, color])
    return colorscale


def build_bottleneck_matrix_fig(df_matrix: pd.DataFrame, commodities: Optional[List[str]] = None) -> go.Figure:
    """System-wide heatmap: rows = segments (Primary Elevators, Process
    Elevators, and each terminal port), columns = commodities, cell color
    = the segment's ALREADY-COMPUTED `bottleneck_tag` (Red > 85% / Yellow
    > 75% / Green otherwise -- the exact same thresholds `bottleneck_matrix()`
    used to compute the tag, read directly rather than recomputed here, so
    this chart's colors can never drift out of sync with the tag itself).
    Cells show the utilization % as text; segment/commodity combinations
    with no capacity data show gray "N/A" rather than a misleading 0%.

    Parameters
    ----------
    df_matrix : pd.DataFrame
        Output of `CGCAnalytics.get_regional_utilization_matrix()` -- one
        row per (grain, segment) at the current snapshot, with
        `capacity_utilization_pct` and `bottleneck_tag` already computed.
    commodities : Optional[List[str]]
        Which commodities to show as columns (default: CORE_COMMODITIES).
    """
    commodities = list(commodities) if commodities else list(CORE_COMMODITIES)
    segments = list(BOTTLENECK_SEGMENT_ORDER)

    if df_matrix.empty:
        fig = go.Figure()
        fig.update_layout(title="Bottleneck Matrix (no data available)", template="plotly_white")
        return fig

    df = df_matrix.copy()
    df["grain"] = df["grain"].apply(to_display_grain_name)  # e.g. 'Amber Durum' -> 'Durum'
    df = df[df["grain"].isin(commodities)]

    pct_grid = df.pivot_table(index="segment", columns="grain", values="capacity_utilization_pct", aggfunc="first")
    tag_grid = df.pivot_table(index="segment", columns="grain", values="bottleneck_tag", aggfunc="first")

    pct_grid = pct_grid.reindex(index=segments, columns=commodities)
    tag_grid = tag_grid.reindex(index=segments, columns=commodities).fillna("N/A")

    z = tag_grid.apply(lambda col: col.map(_BOTTLENECK_TAG_Z)).to_numpy()
    text = pct_grid.apply(lambda col: col.map(lambda v: f"{v:.0f}%" if pd.notna(v) else "N/A")).to_numpy()

    fig = go.Figure(data=go.Heatmap(
        z=z,
        x=commodities,
        y=segments,
        text=text,
        texttemplate="%{text}",
        textfont=dict(size=12),
        colorscale=_discrete_colorscale(_BOTTLENECK_TAG_ORDER, _BOTTLENECK_TAG_COLORS),
        zmin=0, zmax=len(_BOTTLENECK_TAG_ORDER) - 1,
        showscale=False,
        xgap=2, ygap=2,
        hovertemplate="%{y} — %{x}<br>Utilization: %{text}<extra></extra>",
    ))

    # Manual legend (dummy invisible-point traces) since a Plotly colorbar
    # doesn't label discrete categories the way a Red/Yellow/Green/N/A key
    # needs to. Non-interactive for the same reason as the Tab 1 legend --
    # there's nothing meaningful to filter by clicking a color swatch here.
    for tag in ["Green", "Yellow", "Red", "N/A"]:
        fig.add_trace(go.Scatter(
            x=[None], y=[None], mode="markers",
            marker=dict(size=10, color=_BOTTLENECK_TAG_COLORS[tag], symbol="square"),
            name=tag, showlegend=True, hoverinfo="skip",
        ))

    fig.update_layout(
        title="Bottleneck Matrix — System-Wide Utilization Snapshot",
        xaxis=dict(title="Commodity", side="bottom"),
        yaxis=dict(title="", autorange="reversed"),
        template="plotly_white",
        height=max(320, 90 + 55 * len(segments)),
        legend=dict(
            title="Status", orientation="h", yanchor="top", y=-0.15, xanchor="center", x=0.5,
            itemclick=False, itemdoubleclick=False,
        ),
        margin=dict(l=160, r=40, t=60, b=90),
    )
    return fig
