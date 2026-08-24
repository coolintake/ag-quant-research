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

import re
import math
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from cgc_engine import (
    CORE_COMMODITIES, COMMODITY_COLORS, TERMINAL_LOCATIONS, to_display_grain_name,
    UTIL_RED_THRESHOLD, UTIL_YELLOW_THRESHOLD,
)

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


def build_commercial_stocks_summary_table(df_summary: pd.DataFrame) -> pd.DataFrame:
    """Display-ready formatting for the 'Commercial Stocks in Pipeline'
    summary table: Segment | Stocks (KMT) | Capacity (KMT) | Utilization (%).
    """
    def _fmt_volume(v: float) -> str:
        return f"{v:,.0f}" if pd.notna(v) else "N/A"

    def _fmt_pct(v: float) -> str:
        return f"{v:.1f}%" if pd.notna(v) else "N/A"

    out = pd.DataFrame()
    out["Segment"] = df_summary["segment"]
    out["Stocks (KMT)"] = df_summary["stocks_ktonnes"].apply(_fmt_volume)
    out["Capacity (KMT)"] = df_summary["capacity_ktonnes"].apply(_fmt_volume)
    out["Utilization (%)"] = df_summary["pct_utilized"].apply(_fmt_pct)
    return out


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
        line=dict(color="#8B0000", width=1.5), marker=dict(size=4),
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


def build_pacing_summary_table(df_summary: pd.DataFrame) -> pd.DataFrame:
    """Display-ready formatting for the Cumulative Outflow Pacing summary
    table: comma-separated integer volumes (kMT), explicit +/- signed
    percentage variances. Returns a DataFrame of pre-formatted strings --
    kept separate from `CGCAnalytics.get_pacing_summary_table()`'s raw
    numeric output so the underlying numbers stay easy to test and reuse,
    with formatting-only concerns isolated here.

    Parameters
    ----------
    df_summary : pd.DataFrame
        Output of `CGCAnalytics.get_pacing_summary_table()`.
    """
    def _fmt_volume(v: float) -> str:
        return f"{v:,.0f}" if pd.notna(v) else "N/A"

    def _fmt_pct(v: float) -> str:
        return f"{v:+.1f}%" if pd.notna(v) else "N/A"

    out = pd.DataFrame()
    out["Commodity"] = df_summary["commodity"]
    out["YTD (KMT)"] = df_summary["ytd_ktonnes"].apply(_fmt_volume)
    out["Last Yr. (KMT)"] = df_summary["last_yr_ktonnes"].apply(_fmt_volume)
    out["3-Yr Avg (KMT)"] = df_summary["avg3yr_ktonnes"].apply(_fmt_volume)
    out["vs Last Yr. (%)"] = df_summary["vs_last_yr_pct"].apply(_fmt_pct)
    out["vs 3-Yr (%)"] = df_summary["vs_3yr_pct"].apply(_fmt_pct)
    return out


# ═══════════════════════════════════════════════════════════════════════════
# TAB 3 — Bottleneck Matrix (single flat heatmap, provincial Primary rows
# nested directly beneath the national Primary Elevators row)
# ═══════════════════════════════════════════════════════════════════════════

# Row order: national Primary Elevators, its 3 provincial sub-rows
# (Saskatchewan/Alberta/Manitoba), national Process Elevators (NOT
# expanded provincially -- Process capacity is genuinely commodity-specific
# per facility, not per-province, so a provincial split wouldn't mean the
# same thing it does for Primary), then the 6 terminal ports.
BOTTLENECK_SEGMENT_ORDER: List[str] = ["Primary Elevators", "Process Elevators", *TERMINAL_LOCATIONS]

_PRIMARY_PROVINCE_SUBROW_KEYS: List[str] = ["SK Primary", "AB Primary", "MB Primary"]
_PRIMARY_PROVINCE_NODE_TO_SUBROW: Dict[str, str] = {
    "SK": "SK Primary", "AB": "AB Primary", "MB": "MB Primary",
}

BOTTLENECK_ROW_ORDER: List[str] = [
    "Primary Elevators", *_PRIMARY_PROVINCE_SUBROW_KEYS, "Process Elevators", *TERMINAL_LOCATIONS,
]

# Y-axis tick label overrides: provincial sub-rows get indentation, a
# smaller font, and a lighter gray via an inline <span>, so they read as
# nested beneath "Primary Elevators" rather than as peer rows. Any row NOT
# listed here (Primary Elevators, Process Elevators, every terminal port)
# falls back to plain bold in `_render_bottleneck_heatmap`.
_BOTTLENECK_ROW_TICKTEXT: Dict[str, str] = {
    "SK Primary": '<span style="font-size:12px; color:#555555;">&nbsp;&nbsp;&nbsp;&nbsp;↳ Saskatchewan</span>',
    "AB Primary": '<span style="font-size:12px; color:#555555;">&nbsp;&nbsp;&nbsp;&nbsp;↳ Alberta</span>',
    "MB Primary": '<span style="font-size:12px; color:#555555;">&nbsp;&nbsp;&nbsp;&nbsp;↳ Manitoba</span>',
}

# Status colors, unchanged from before -- only the LEGEND LABELS below are
# new. The underlying "Green"/"Yellow"/"Red"/"N/A" tag values are untouched
# (they're the same strings `bottleneck_matrix()` computes elsewhere in the
# app), so run_report.py and every other consumer of that field is
# unaffected -- this is purely a display-label change for this one chart.
_BOTTLENECK_TAG_STYLE: Dict[str, Dict[str, object]] = {
    "N/A":    {"bg": "#EEEEEE", "text": "#999999", "bold": False},
    "Green":  {"bg": "#E3F2E6", "text": "#3A7D44", "bold": False},
    "Yellow": {"bg": "#FCEEBB", "text": "#8A6D00", "bold": False},
    "Red":    {"bg": "#D62728", "text": "#FFFFFF", "bold": True},
}

# Softened variant for the provincial sub-rows nested under Primary
# Elevators, so they read as visually secondary/"child" rows without
# losing legibility. This is NOT a uniform opacity fade of the main
# palette -- Red's text color is deliberately re-picked (dark maroon, not
# white) because its background is much lighter here; white-on-pale-pink
# would fail the "text stays fully readable" requirement this exists to
# satisfy. "bold" stays tied to severity (matches the main palette), not
# to row hierarchy -- a real provincial bottleneck should still stand out,
# just less loudly than the same status at the national level.
_BOTTLENECK_TAG_STYLE_SUBROW: Dict[str, Dict[str, object]] = {
    "N/A":    {"bg": "#F6F6F6", "text": "#999999", "bold": False},
    "Green":  {"bg": "#F3FAF4", "text": "#5FA06B", "bold": False},
    "Yellow": {"bg": "#FDF8E7", "text": "#B79A4A", "bold": False},
    "Red":    {"bg": "#F3C9C9", "text": "#8B2020", "bold": True},
}
_BOTTLENECK_TAG_ORDER: List[str] = ["N/A", "Green", "Yellow", "Red"]
_BOTTLENECK_TAG_Z: Dict[str, int] = {tag: i for i, tag in enumerate(_BOTTLENECK_TAG_ORDER)}
_BOTTLENECK_LEGEND_LABELS: Dict[str, str] = {
    "Green": "Normal (< 75%)",
    "Yellow": "Watch (75–85%)",
    "Red": "Throttling (> 85%)",
    "N/A": "N/A (No Data)",
}


def _discrete_colorscale(tags_in_order: List[str], style_map: Dict[str, Dict[str, object]]) -> list:
    """Build a Plotly colorscale with hard (non-interpolated) bands, one
    per category in `tags_in_order`, each occupying an equal-width slice.
    """
    n = len(tags_in_order)
    colorscale = []
    for i, tag in enumerate(tags_in_order):
        color = style_map[tag]["bg"]
        colorscale.append([i / n, color])
        colorscale.append([(i + 1) / n, color])
    return colorscale


def _classify_utilization(pct: float) -> str:
    """Red > 85% / Yellow > 75% / Green otherwise -- the same threshold
    values as `bottleneck_matrix()` in cgc_engine.py, applied directly
    here for cells (Total column, provincial rows) that don't have a
    pre-computed bottleneck_tag to read from.
    """
    if pct > UTIL_RED_THRESHOLD:
        return "Red"
    if pct > UTIL_YELLOW_THRESHOLD:
        return "Yellow"
    return "Green"


def _render_bottleneck_heatmap(
    pct_grid: pd.DataFrame,
    tag_grid: pd.DataFrame,
    title: str,
    row_ticktext_map: Optional[Dict[str, str]] = None,
    soft_style_rows: Optional[set] = None,
) -> go.Figure:
    """Shared heatmap renderer for the Bottleneck Matrix. Both axes use
    numeric positions with custom `ticktext` (rather than Plotly's default
    category axis) so individual tick labels can carry their own HTML
    styling -- bold column/row headers, a smaller indented gray style for
    provincial sub-rows, and a bold "Total" column header, none of which a
    single uniform `tickfont` could produce.

    Rows in `soft_style_rows` (the provincial sub-rows) render with
    `_BOTTLENECK_TAG_STYLE_SUBROW` instead of the main palette, via a
    SECOND overlaid Heatmap trace -- Plotly's colorscale maps z-values to
    colors once per trace, so getting the same status (e.g. Red) to render
    differently for different rows genuinely requires two traces, each
    masked to NaN outside the rows it owns (NaN cells render fully
    transparent, so the two traces combine into one seamless grid).

    Cell text is drawn as individual annotations rather than the Heatmap's
    built-in `text`/`texttemplate`, since Plotly only supports one
    textfont color per trace -- which can't give Red cells bold white text
    while Green/Yellow/N-A get quiet dark text, and couldn't distinguish
    main-row from sub-row text color either.

    The "Total" column -- both its cell values and its x-axis header -- is
    always bold, regardless of that cell's own status color, to visually
    separate an aggregate figure from individual commodity cells. Rows not
    present in `row_ticktext_map` default to plain bold.
    """
    rows = list(pct_grid.index)
    columns = list(pct_grid.columns)
    x_pos = list(range(len(columns)))
    y_pos = list(range(len(rows)))
    soft_style_rows = soft_style_rows or set()

    z_all = tag_grid.apply(lambda col: col.map(_BOTTLENECK_TAG_Z)).to_numpy().astype(float)
    is_soft_row = np.array([r in soft_style_rows for r in rows])
    z_main = np.where(is_soft_row[:, None], np.nan, z_all)
    z_soft = np.where(is_soft_row[:, None], z_all, np.nan)

    row_ticktext_map = row_ticktext_map or {}

    def _plain_text_row_label(row: str) -> str:
        html = row_ticktext_map.get(row, row)
        plain = re.sub(r"<[^>]+>", "", html).replace("&nbsp;", "").strip()
        return plain or row

    hover_text = [
        [f"{_plain_text_row_label(row)}<br>{col}: {pct_grid.loc[row, col]:.0f}%"
         if pd.notna(pct_grid.loc[row, col]) else f"{_plain_text_row_label(row)}<br>{col}: N/A"
         for col in columns]
        for row in rows
    ]

    fig = go.Figure()
    fig.add_trace(go.Heatmap(
        z=z_main, x=x_pos, y=y_pos,
        text=hover_text, hoverinfo="text",
        colorscale=_discrete_colorscale(_BOTTLENECK_TAG_ORDER, _BOTTLENECK_TAG_STYLE),
        zmin=0, zmax=len(_BOTTLENECK_TAG_ORDER) - 1,
        showscale=False, xgap=3, ygap=3,
    ))
    fig.add_trace(go.Heatmap(
        z=z_soft, x=x_pos, y=y_pos,
        text=hover_text, hoverinfo="text",
        colorscale=_discrete_colorscale(_BOTTLENECK_TAG_ORDER, _BOTTLENECK_TAG_STYLE_SUBROW),
        zmin=0, zmax=len(_BOTTLENECK_TAG_ORDER) - 1,
        showscale=False, xgap=3, ygap=3,
    ))

    for row_i, row_name in enumerate(rows):
        style_map = _BOTTLENECK_TAG_STYLE_SUBROW if row_name in soft_style_rows else _BOTTLENECK_TAG_STYLE
        for col_i, col_name in enumerate(columns):
            tag = tag_grid.loc[row_name, col_name]
            style = style_map[tag]
            pct = pct_grid.loc[row_name, col_name]
            label = f"{pct:.0f}%" if pd.notna(pct) else "N/A"
            bold = style["bold"] or col_name == "Total"
            text = f"<b>{label}</b>" if bold else label
            fig.add_annotation(
                x=col_i, y=row_i, text=text, showarrow=False,
                font=dict(size=12, color=style["text"]),
            )

    if "Total" in columns:
        # A thin visual divider between the regular columns and the Total
        # column, so it reads as a summary rather than just another column.
        total_idx = columns.index("Total")
        fig.add_shape(
            type="line", xref="x", yref="paper",
            x0=total_idx - 0.5, x1=total_idx - 0.5, y0=0, y1=1,
            line=dict(color="#666666", width=1.5),
        )

    xticktext = [f"<b>{c}</b>" if c == "Total" else c for c in columns]
    yticktext = [row_ticktext_map.get(r, f"<b>{r}</b>") for r in rows]

    # Compact vertical legend, right of the plot, with plain-language
    # status labels rather than raw color names -- non-interactive for the
    # same reason as the Tab 1 legend: there's nothing meaningful to
    # filter by clicking a color swatch here. Kept to the 4 main-palette
    # swatches only -- the sub-row softening is a visual-hierarchy device,
    # not a new status category, so it doesn't need its own legend entry.
    for tag in ["Green", "Yellow", "Red", "N/A"]:
        fig.add_trace(go.Scatter(
            x=[None], y=[None], mode="markers",
            marker=dict(size=10, color=_BOTTLENECK_TAG_STYLE[tag]["bg"], symbol="square",
                        line=dict(width=1, color="#999999")),
            name=_BOTTLENECK_LEGEND_LABELS[tag], showlegend=True, hoverinfo="skip",
        ))

    fig.update_layout(
        title=title,
        xaxis=dict(title="Commodity", tickmode="array", tickvals=x_pos, ticktext=xticktext, side="bottom"),
        yaxis=dict(title="", tickmode="array", tickvals=y_pos, ticktext=yticktext, autorange="reversed"),
        template="plotly_white",
        height=max(320, 90 + 55 * len(rows)),
        legend=dict(
            orientation="v", x=1.02, y=1, xanchor="left", yanchor="top",
            title="Status", itemclick=False, itemdoubleclick=False,
        ),
        margin=dict(l=200, r=170, t=60, b=60),
    )
    return fig


def build_bottleneck_matrix_fig(
    df_matrix: pd.DataFrame,
    df_province_snapshot: pd.DataFrame,
    commodities: Optional[List[str]] = None,
) -> go.Figure:
    """Single system-wide heatmap: national Primary Elevators with its
    Saskatchewan/Alberta/Manitoba breakdown nested directly beneath it,
    national Process Elevators (not expanded provincially), and the 6
    terminal ports -- columns are commodities plus a bold "Total" column.

    Cell color for the NATIONAL rows (Primary Elevators, Process
    Elevators, terminals) reads the ALREADY-COMPUTED `bottleneck_tag`
    directly (Red > 85% / Yellow > 75% / Green otherwise), so it can never
    drift out of sync with the tag itself. Provincial sub-row and Total
    column cells have no pre-existing tag to read (they're aggregates this
    chart computes), so they're classified with `_classify_utilization`
    using the EXACT SAME threshold constants -- not independently
    duplicated thresholds that could quietly diverge.

    Total column / provincial denominator math -- NOT a naive
    sum-everything, on purpose: Primary Elevators (national AND each
    province) and terminal ports share ONE physical capacity pool across
    every commodity (storage bins aren't commodity-specific), so their
    Total is (sum of selected grains' stocks) / (that one shared capacity
    value). Process Elevators' capacity is genuinely commodity-specific
    (canola crush vs. barley malting aren't the same equipment), so its
    Total is (sum of stocks) / (SUM of each selected grain's own
    capacity). Using the wrong formula for either case would silently
    reintroduce the exact capacity-conflation bug fixed earlier in this
    project.

    Parameters
    ----------
    df_matrix : pd.DataFrame
        Output of `CGCAnalytics.get_regional_utilization_matrix()` -- the
        national rows.
    df_province_snapshot : pd.DataFrame
        Output of `CGCAnalytics.get_segment_capacity_snapshot('primary_province', ...)`
        -- the provincial Primary Elevators breakdown.
    commodities : Optional[List[str]]
        Which commodities to include (default: CORE_COMMODITIES). The
        Total column and every provincial cell recalculate from exactly
        this selection. Columns always render in CORE_COMMODITIES'
        canonical order regardless of the order passed in.
    """
    commodities = list(commodities) if commodities else list(CORE_COMMODITIES)
    commodities = [c for c in CORE_COMMODITIES if c in commodities] + \
                  [c for c in commodities if c not in CORE_COMMODITIES]

    if df_matrix.empty:
        fig = go.Figure()
        fig.update_layout(title="Bottleneck Matrix (no data available)", template="plotly_white")
        return fig

    # -- national rows: Primary Elevators, Process Elevators, terminals --
    df = df_matrix.copy()
    df["grain"] = df["grain"].apply(to_display_grain_name)  # e.g. 'Amber Durum' -> 'Durum'
    df = df[df["grain"].isin(commodities)]

    national_rows = list(BOTTLENECK_SEGMENT_ORDER)
    pct_grid = df.pivot_table(index="segment", columns="grain", values="capacity_utilization_pct", aggfunc="first")
    tag_grid = df.pivot_table(index="segment", columns="grain", values="bottleneck_tag", aggfunc="first")
    stocks_grid = df.pivot_table(index="segment", columns="grain", values="stocks_ktonnes", aggfunc="first")
    capacity_grid = df.pivot_table(index="segment", columns="grain", values="capacity_ktonnes", aggfunc="first")
    is_specific_by_segment = df.groupby("segment")["capacity_is_commodity_specific"].first()

    pct_grid = pct_grid.reindex(index=national_rows, columns=commodities)
    tag_grid = tag_grid.reindex(index=national_rows, columns=commodities).fillna("N/A")
    stocks_grid = stocks_grid.reindex(index=national_rows, columns=commodities)
    capacity_grid = capacity_grid.reindex(index=national_rows, columns=commodities)

    national_total_pct: Dict[str, float] = {}
    national_total_tag: Dict[str, str] = {}
    for segment in national_rows:
        total_stocks = stocks_grid.loc[segment].sum(skipna=True)
        is_specific = bool(is_specific_by_segment.get(segment, False))
        if is_specific:
            total_capacity = capacity_grid.loc[segment].sum(skipna=True)
        else:
            non_null_capacity = capacity_grid.loc[segment].dropna()
            total_capacity = non_null_capacity.iloc[0] if not non_null_capacity.empty else 0.0

        if total_capacity and total_capacity > 0:
            national_total_pct[segment] = total_stocks / total_capacity * 100.0
            national_total_tag[segment] = _classify_utilization(national_total_pct[segment])
        else:
            national_total_pct[segment] = float("nan")
            national_total_tag[segment] = "N/A"

    # -- provincial sub-rows: SK / AB / MB Primary --
    prov_df = df_province_snapshot[df_province_snapshot["node"].isin(_PRIMARY_PROVINCE_NODE_TO_SUBROW)].copy()
    prov_df["row_key"] = prov_df["node"].map(_PRIMARY_PROVINCE_NODE_TO_SUBROW)
    prov_df = prov_df.set_index("row_key").reindex(_PRIMARY_PROVINCE_SUBROW_KEYS) if not prov_df.empty else prov_df

    prov_pct = pd.DataFrame(index=_PRIMARY_PROVINCE_SUBROW_KEYS, columns=commodities, dtype=float)
    prov_tag = pd.DataFrame(index=_PRIMARY_PROVINCE_SUBROW_KEYS, columns=commodities, dtype=object)
    prov_total_pct: Dict[str, float] = {}
    prov_total_tag: Dict[str, str] = {}

    for row_key in _PRIMARY_PROVINCE_SUBROW_KEYS:
        capacity = prov_df.loc[row_key, "capacity_ktonnes"] if (not prov_df.empty and row_key in prov_df.index) else float("nan")
        stocks_sum, any_stock = 0.0, False
        for commodity in commodities:
            stock = (
                prov_df.loc[row_key, commodity]
                if (not prov_df.empty and row_key in prov_df.index and commodity in prov_df.columns)
                else float("nan")
            )
            if pd.notna(capacity) and capacity > 0 and pd.notna(stock):
                pct = stock / capacity * 100.0
                tag = _classify_utilization(pct)
                stocks_sum += stock
                any_stock = True
            else:
                pct, tag = float("nan"), "N/A"
            prov_pct.loc[row_key, commodity] = pct
            prov_tag.loc[row_key, commodity] = tag

        if pd.notna(capacity) and capacity > 0 and any_stock:
            prov_total_pct[row_key] = stocks_sum / capacity * 100.0
            prov_total_tag[row_key] = _classify_utilization(prov_total_pct[row_key])
        else:
            prov_total_pct[row_key] = float("nan")
            prov_total_tag[row_key] = "N/A"

    # -- assemble the combined grid in the requested row order --
    row_order = BOTTLENECK_ROW_ORDER
    columns_with_total = commodities + ["Total"]
    combined_pct = pd.DataFrame(index=row_order, columns=columns_with_total, dtype=float)
    combined_tag = pd.DataFrame(index=row_order, columns=columns_with_total, dtype=object)

    for row in national_rows:
        combined_pct.loc[row, commodities] = pct_grid.loc[row].values
        combined_tag.loc[row, commodities] = tag_grid.loc[row].values
        combined_pct.loc[row, "Total"] = national_total_pct[row]
        combined_tag.loc[row, "Total"] = national_total_tag[row]

    for row in _PRIMARY_PROVINCE_SUBROW_KEYS:
        combined_pct.loc[row, commodities] = prov_pct.loc[row].values
        combined_tag.loc[row, commodities] = prov_tag.loc[row].values
        combined_pct.loc[row, "Total"] = prov_total_pct[row]
        combined_tag.loc[row, "Total"] = prov_total_tag[row]

    combined_tag = combined_tag.fillna("N/A")

    return _render_bottleneck_heatmap(
        combined_pct, combined_tag,
        title="Bottleneck Matrix — System-Wide Utilization Snapshot",
        row_ticktext_map=_BOTTLENECK_ROW_TICKTEXT,
        soft_style_rows=set(_PRIMARY_PROVINCE_SUBROW_KEYS),
    )


# ═══════════════════════════════════════════════════════════════════════════
# TAB — Seasonal Pacing Anomaly (Z-Score Divergence)
# ═══════════════════════════════════════════════════════════════════════════

# Same visual-hierarchy principle as the Bottleneck Matrix: only the
# genuinely unusual result should visually shout. |z| < 1 (within one
# historical standard deviation) is normal -- pale gray, easy to skim
# past. 1 <= |z| < 2 is worth noticing -- a muted color. |z| >= 2 is a
# real outlier -- bold and saturated. Direction gets its own hue (blue =
# running faster than history, orange = running slower) rather than a
# red/green good-or-bad judgment, since "faster" isn't inherently good or
# bad -- that depends on context this chart doesn't have.
PACING_NORMAL_THRESHOLD: float = 1.0
PACING_EXTREME_THRESHOLD: float = 2.0

_PACING_STYLE: Dict[str, Dict[str, object]] = {
    "normal":        {"bg": "#EEEEEE", "text": "#999999", "bold": False},
    "fast_elevated": {"bg": "#AED6F1", "text": "#154360", "bold": False},
    "fast_extreme":  {"bg": "#1F618D", "text": "#FFFFFF", "bold": True},
    "slow_elevated": {"bg": "#F5CBA7", "text": "#7E5109", "bold": False},
    "slow_extreme":  {"bg": "#CA6F1E", "text": "#FFFFFF", "bold": True},
    "na":            {"bg": "#EEEEEE", "text": "#999999", "bold": False},
}


def _pacing_style_key(z: float) -> str:
    if pd.isna(z):
        return "na"
    abs_z = abs(z)
    if abs_z < PACING_NORMAL_THRESHOLD:
        return "normal"
    direction = "fast" if z > 0 else "slow"
    magnitude = "extreme" if abs_z >= PACING_EXTREME_THRESHOLD else "elevated"
    return f"{direction}_{magnitude}"


def _z_to_percentile(z: float) -> float:
    """Approximate percentile (0-100) a Z-score corresponds to under a
    standard normal distribution, via the standard normal CDF -- used to
    translate an abstract Z-score into an intuitive 'Nth percentile'
    label for a reader without a statistics background. Uses `math.erf`
    (stdlib) rather than adding a scipy dependency for one calculation.
    """
    return 0.5 * (1.0 + math.erf(abs(z) / math.sqrt(2))) * 100.0


def _format_anomaly_label(z: float, ratio: float) -> str:
    """Plain-language anomaly label -- e.g. '2.4x Normal Pace (99th
    Percentile Anomaly)' instead of a bare '+6.00σ'. `ratio` is the
    current week's outflow divided by its historical average (a simple,
    intuitive multiplier); the percentile is derived from the Z-score
    itself. The raw Z-score remains available in the "Underlying data"
    expander for anyone who wants it -- this is a display-layer
    translation, not a loss of the underlying number.
    """
    if pd.isna(z):
        return "N/A"
    percentile = _z_to_percentile(z)
    pct_text = "99.9th+ Percentile" if percentile >= 99.9 else f"{percentile:.0f}th Percentile"
    if pd.notna(ratio):
        return f"{ratio:.1f}x Normal Pace ({pct_text} Anomaly)"
    return f"{pct_text} Anomaly"


def _format_anomaly_short_label(z: float, ratio: float) -> str:
    """Compact version of `_format_anomaly_label` for the always-visible
    per-bar text (the full '(Nth Percentile Anomaly)' qualifier is
    reserved for hover, where space isn't constrained by 6 bars needing
    their own label simultaneously).
    """
    if pd.isna(z):
        return "N/A"
    if pd.notna(ratio):
        return f"{ratio:.1f}x Normal Pace"
    percentile = _z_to_percentile(z)
    return "99.9th+ %ile" if percentile >= 99.9 else f"{percentile:.0f}th %ile"


def build_seasonal_pacing_fig(df_anomaly: pd.DataFrame, commodities: Optional[List[str]] = None) -> go.Figure:
    """Diverging horizontal bar chart of each commodity's current-week
    outflow pace, expressed as a Z-score against its historical average
    (positive = running faster than history, negative = running slower).

    Sorted by Z-score value rather than CORE_COMMODITIES' canonical order
    -- a deliberate exception to the ordering convention used elsewhere in
    this app: a diverging bar chart is meaningfully easier to read sorted
    by magnitude (extremes at each end) than in a fixed commodity order.

    Parameters
    ----------
    df_anomaly : pd.DataFrame
        Output of `CGCAnalytics.get_seasonal_pacing_anomaly()` -- one row
        per commodity at the current (crop_year, grain_week) snapshot,
        with `z_score`, `anomaly_ktonnes`, `hist_avg`, and
        `current_outflow_ktonnes` already computed.
    commodities : Optional[List[str]]
        Which commodities to include (default: CORE_COMMODITIES).
    """
    commodities = list(commodities) if commodities else list(CORE_COMMODITIES)

    if df_anomaly.empty:
        fig = go.Figure()
        fig.update_layout(title="Seasonal Pacing Anomaly (no data available)", template="plotly_white")
        return fig

    df = df_anomaly[df_anomaly["grain"].isin(commodities)].copy()
    df = df.set_index("grain").reindex(commodities).reset_index()
    df = df.sort_values("z_score", na_position="first", ascending=True).reset_index(drop=True)

    bg_colors, text_colors, bold_flags, hover_texts = [], [], [], []
    for _, row in df.iterrows():
        key = _pacing_style_key(row["z_score"])
        style = _PACING_STYLE[key]
        bg_colors.append(style["bg"])
        text_colors.append(style["text"])
        bold_flags.append(style["bold"])
        ratio = (
            row["current_outflow_ktonnes"] / row["hist_avg"]
            if pd.notna(row["hist_avg"]) and row["hist_avg"] != 0 else float("nan")
        )
        if pd.notna(row["z_score"]):
            hover_texts.append(
                f"<b>{row['grain']}</b><br>"
                f"{_format_anomaly_label(row['z_score'], ratio)}<br>"
                f"Current: {row['current_outflow_ktonnes']:,.1f} Kt<br>"
                f"{row.get('hist_pool', '')}-yr avg: {row['hist_avg']:,.1f} Kt<br>"
                f"Deviation: {row['anomaly_ktonnes']:+,.1f} Kt"
            )
        else:
            hover_texts.append(f"<b>{row['grain']}</b><br>No historical baseline available")

    z_display = df["z_score"].fillna(0.0)

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=z_display,
        y=df["grain"],
        orientation="h",
        marker_color=bg_colors,
        hovertext=hover_texts,
        hoverinfo="text",
    ))

    for _, row in df.iterrows():
        z = row["z_score"]
        key = _pacing_style_key(z)
        style = _PACING_STYLE[key]
        ratio = (
            row["current_outflow_ktonnes"] / row["hist_avg"]
            if pd.notna(row["hist_avg"]) and row["hist_avg"] != 0 else float("nan")
        )
        label = _format_anomaly_short_label(z, ratio)
        text = f"<b>{label}</b>" if style["bold"] else label
        x_pos = z if pd.notna(z) else 0.0
        offset = 0.08 * max(abs(df["z_score"].fillna(0)).max(), 1.0)
        if x_pos >= 0:
            x_annot, xanchor = x_pos + offset, "left"
        else:
            x_annot, xanchor = x_pos - offset, "right"
        fig.add_annotation(
            x=x_annot, y=row["grain"], text=text, showarrow=False,
            xanchor=xanchor, font=dict(size=12, color="#000000"),
        )

    fig.add_vline(x=0, line_color="#333333", line_width=1.5)
    for threshold in (PACING_NORMAL_THRESHOLD, PACING_EXTREME_THRESHOLD):
        fig.add_vline(x=threshold, line_dash="dot", line_color="#AAAAAA", line_width=1)
        fig.add_vline(x=-threshold, line_dash="dot", line_color="#AAAAAA", line_width=1)

    max_abs_z = max(abs(z_display).max(), PACING_EXTREME_THRESHOLD) * 1.3

    fig.update_layout(
        title="Seasonal Pacing Anomaly — Z-Score Divergence from Historical Pace",
        xaxis=dict(title="Z-score (σ from historical average)", range=[-max_abs_z, max_abs_z], zeroline=False),
        yaxis=dict(title=""),
        template="plotly_white",
        height=max(320, 80 + 45 * len(df)),
        showlegend=False,
        margin=dict(l=140, r=60, t=60, b=60),
    )
    return fig


# ═══════════════════════════════════════════════════════════════════════════
# TAB — Producer Deliveries
# ═══════════════════════════════════════════════════════════════════════════

_DELIVERY_MAIN_STREAMS: List[str] = ["Western Canada", "Process (National)", "Total Producer Deliveries"]
_DELIVERY_PROVINCES: List[str] = ["SK", "AB", "MB", "BC"]

# Display-only rename -- the facade's internal region key stays "Western
# Canada" (used for lookups, e.g. the Calculator), only the LABEL shown
# in these tables gets the "(Primary)" qualifier per the spec.
_DELIVERY_REGION_DISPLAY_LABELS: Dict[str, str] = {"Western Canada": "Western Canada (Primary)"}


def _format_delivery_table(
    df_summary: pd.DataFrame, regions: List[str],
    volume_col: str, volume_label: str, yoy_col: str, avg3yr_col: str,
) -> pd.DataFrame:
    """Shared formatter for all 4 Producer Deliveries display tables
    (weekly/YTD x main-streams/provincial) -- same 'Region / Stream' +
    volume + YoY + 3Y Avg Delta column shape, just pointed at different
    source columns and a different region subset, so the four variants
    can never format inconsistently with each other.
    """
    def _fmt_volume(v: float) -> str:
        return f"{v:,.0f}" if pd.notna(v) else "N/A"

    def _fmt_pct(v: float) -> str:
        return f"{v:+.1f}%" if pd.notna(v) else "N/A"

    sub = df_summary.set_index("region").reindex(regions)
    out = pd.DataFrame()
    out["Region / Stream"] = [_DELIVERY_REGION_DISPLAY_LABELS.get(r, r) for r in regions]
    out[volume_label] = sub[volume_col].apply(_fmt_volume).values
    out["YoY Change (%)"] = sub[yoy_col].apply(_fmt_pct).values
    out["3Y Avg Delta (%)"] = sub[avg3yr_col].apply(_fmt_pct).values
    return out


def build_weekly_deliveries_table(df_summary: pd.DataFrame) -> pd.DataFrame:
    """Table 1 (main rows): Western Canada (Primary) / Process (National) /
    Total Producer Deliveries, current-week volume and its own weekly-basis
    YoY / 3-Yr Avg comparisons (this week vs. the SAME WEEK last year /
    3-yr average -- not the cumulative YTD comparison Table 2 uses).
    """
    return _format_delivery_table(
        df_summary, _DELIVERY_MAIN_STREAMS,
        "current_week_ktonnes", "Volume (KMT)", "week_yoy_pct", "week_avg3yr_delta_pct",
    )


def build_weekly_provincial_table(df_summary: pd.DataFrame) -> pd.DataFrame:
    """Table 1's provincial breakdown (SK/AB/MB/BC), same weekly-basis
    columns as `build_weekly_deliveries_table` -- shown inside the
    'Show Weekly Provincial Breakdown' expander.
    """
    return _format_delivery_table(
        df_summary, _DELIVERY_PROVINCES,
        "current_week_ktonnes", "Volume (KMT)", "week_yoy_pct", "week_avg3yr_delta_pct",
    )


def build_ytd_deliveries_table(df_summary: pd.DataFrame) -> pd.DataFrame:
    """Table 2 (main rows): Western Canada (Primary) / Process (National) /
    Total Producer Deliveries, cumulative YTD volume and its own
    YTD-basis YoY / 3-Yr Avg comparisons (YTD vs. YTD-at-the-same-week
    last year / 3-yr average -- not the weekly comparison Table 1 uses).
    """
    return _format_delivery_table(
        df_summary, _DELIVERY_MAIN_STREAMS,
        "ytd_ktonnes", "YTD (KMT)", "ytd_yoy_pct", "ytd_avg3yr_delta_pct",
    )


def build_ytd_provincial_table(df_summary: pd.DataFrame) -> pd.DataFrame:
    """Table 2's provincial breakdown (SK/AB/MB/BC), same YTD-basis
    columns as `build_ytd_deliveries_table` -- shown inside the
    'Show YTD Provincial Breakdown' expander.
    """
    return _format_delivery_table(
        df_summary, _DELIVERY_PROVINCES,
        "ytd_ktonnes", "YTD (KMT)", "ytd_yoy_pct", "ytd_avg3yr_delta_pct",
    )


def build_producer_deliveries_pacing_fig(df_pacing: pd.DataFrame, subtitle: str = "Total Producer Deliveries") -> go.Figure:
    """Cumulative Producer Deliveries (KMT) across the crop year: current
    crop year (solid, bold), the single most-recent prior crop year (its
    own dashed line -- not blended into the historical envelope), and the
    lookback-year historical average (dotted) with a shaded min/max
    envelope. Styling matches `build_cumulative_pacing_fig`'s established
    conventions (same envelope fill, same current-year color/width) so
    the two pacing charts read as one consistent visual language.

    Parameters
    ----------
    df_pacing : pd.DataFrame
        Output of `CGCAnalytics.get_producer_deliveries_pacing()`.
    subtitle : str
        Optional extra context for the title (e.g. the active province
        filter), appended after an em dash.
    """
    fig = go.Figure()
    title = "Cumulative Producer Deliveries (KMT)"
    if subtitle:
        title = f"{title} — {subtitle}"

    if df_pacing.empty:
        fig.update_layout(
            title=f"{title} (no data available)",
            xaxis=dict(title="Crop Week", range=[1, 52]),
            yaxis=dict(title="Cumulative Deliveries (KMT)"),
            template="plotly_white",
        )
        return fig

    weeks = df_pacing["grain_week"]

    # Shaded 3-year historical min/max envelope -- identical convention to
    # build_cumulative_pacing_fig (draw max invisibly, then min with
    # fill='tonexty' to shade the area between the two).
    fig.add_trace(go.Scatter(
        x=weeks, y=df_pacing["hist_max_ktonnes"], mode="lines",
        line=dict(width=0), showlegend=False, hoverinfo="skip",
    ))
    fig.add_trace(go.Scatter(
        x=weeks, y=df_pacing["hist_min_ktonnes"], mode="lines",
        line=dict(width=0), fill="tonexty", fillcolor="rgba(180, 180, 180, 0.3)",
        name="3-Yr Historical Range", hoverinfo="skip",
    ))

    # 3-Year Average -- dotted, per spec (distinct from the dashed line
    # used for the single prior year, so the two aren't visually confused).
    fig.add_trace(go.Scatter(
        x=weeks, y=df_pacing["hist_avg_ktonnes"], mode="lines",
        line=dict(color="gray", dash="dot", width=2), name="3-Yr Average",
    ))

    # Prior Crop Year -- its own dashed line, distinct from the blended
    # historical envelope above.
    if "prior_yr_cum_ktonnes" in df_pacing.columns and df_pacing["prior_yr_cum_ktonnes"].notna().any():
        fig.add_trace(go.Scatter(
            x=weeks, y=df_pacing["prior_yr_cum_ktonnes"], mode="lines",
            line=dict(color="#4A6FA5", dash="dash", width=2), name="Prior Crop Year",
        ))

    # Current Crop Year -- same solid dark-red convention as the Outflow
    # Pacing chart's current-year line, for a consistent visual language
    # across both pacing tabs.
    crop_year_label = df_pacing["crop_year"].iloc[0]
    fig.add_trace(go.Scatter(
        x=weeks, y=df_pacing["current_cum_ktonnes"], mode="lines+markers",
        line=dict(color="#8B0000", width=1.5), marker=dict(size=4),
        name=f"{crop_year_label} (Current)",
    ))

    fig.update_layout(
        title=title,
        xaxis=dict(title="Crop Week", range=[1, 52], dtick=4),
        yaxis=dict(title="Cumulative Deliveries (KMT)"),
        template="plotly_white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
    )
    return fig
