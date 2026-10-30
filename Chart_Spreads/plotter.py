"""Seasonal spaghetti charts for calendar spreads (Plotly, with a Matplotlib fallback).

History years are drawn as thin muted lines (older = lighter), the highlighted year
as a thick red line, plus an optional historical average line and min/max band.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from spread_engine import seasonal_stats

HIGHLIGHT_COLOR = "#d62728"   # active / most recent crop year
AVERAGE_COLOR = "#1f2937"     # historical average (dashed)
BAND_COLOR = "rgba(100, 116, 139, 0.15)"
GRID_COLOR = "#e5e7eb"


def _history_colors(n: int) -> list[str]:
    """Gray ramp from light (oldest) to dark (newest) slate for history lines."""
    if n <= 0:
        return []
    light, dark = np.array([203, 213, 225]), np.array([71, 85, 105])
    return [f"rgb({r},{g},{b})" for r, g, b in
            (np.linspace(light, dark, n).round().astype(int))]


def _resolve_highlight(spread_df: pd.DataFrame, highlight_year: int | None) -> int | None:
    years = sorted(spread_df["CropYear"].unique())
    if not years:
        return None
    return highlight_year if highlight_year in years else years[-1]


def plot_spaghetti(spread_df: pd.DataFrame, title: str | None = None,
                   highlight_year: int | None = None, show_average: bool = True,
                   show_band: bool = True, avg_years: int = 5, x_axis: str = "SeasonalDate",
                   units: str = "", height: int = 560, range_slider: bool = True):
    """Interactive Plotly spaghetti chart.

    x_axis : 'SeasonalDate' (month ticks, all years on one calendar) or 'DaysToExpiry'
    highlight_year : crop year to emphasize (defaults to the most recent in the data)
    avg_years : number of completed years used for the average line / min-max band
    """
    import plotly.graph_objects as go

    if spread_df.empty:
        raise ValueError("Spread frame is empty: no overlapping dates for this pair.")

    label = spread_df["Label"].iloc[0]
    title = title or f"{label} seasonal spread"
    hi = _resolve_highlight(spread_df, highlight_year)
    years = sorted(spread_df["CropYear"].unique())
    history = [y for y in years if y != hi]
    colors = dict(zip(history, _history_colors(len(history))))
    unit_sfx = f" {units}" if units else ""

    hover = ("<b>%{customdata[0]}</b>  ·  %{customdata[1]}<br>"
             "Spread: <b>%{y:.2f}</b>" + unit_sfx + "<br>"
             "%{customdata[2]}: %{customdata[3]:.2f}<br>"
             "%{customdata[4]}: %{customdata[5]:.2f}<br>"
             "Days to Leg 1 delivery: %{customdata[6]}<extra></extra>")

    fig = go.Figure()

    if show_average or show_band:
        stats = seasonal_stats(spread_df, last_n=avg_years)
        if not stats.empty:
            sx = stats["SeasonalDate"] if x_axis == "SeasonalDate" else stats["DaysToExpiry"]
            yrs = stats["Years"].iloc[0]
            if show_band:
                fig.add_trace(go.Scatter(x=sx, y=stats["Max"], mode="lines", line=dict(width=0),
                                         hoverinfo="skip", showlegend=False))
                fig.add_trace(go.Scatter(x=sx, y=stats["Min"], mode="lines", line=dict(width=0),
                                         fill="tonexty", fillcolor=BAND_COLOR,
                                         name=f"Min-max {yrs}", hoverinfo="skip"))
            if show_average:
                fig.add_trace(go.Scatter(
                    x=sx, y=stats["Mean"], mode="lines", name=f"Avg {yrs}",
                    line=dict(color=AVERAGE_COLOR, width=2, dash="dash"),
                    hovertemplate=f"Avg {yrs}: " + "%{y:.2f}" + unit_sfx + "<extra></extra>"))

    for y in history + ([hi] if hi is not None else []):
        d = spread_df[spread_df["CropYear"] == y]
        x = d["SeasonalDate"] if x_axis == "SeasonalDate" else d["DaysToExpiry"]
        is_hi = y == hi
        # Plain Python values per point so numbers stay numeric for d3 formatting.
        custom = list(zip(d["CropYear"].astype(int), d["Date"].dt.strftime("%Y-%m-%d"),
                          d["Leg1Symbol"], d["Leg1Close"].astype(float),
                          d["Leg2Symbol"], d["Leg2Close"].astype(float),
                          d["DaysToExpiry"].astype(int)))
        fig.add_trace(go.Scatter(
            x=x, y=d["Spread"], mode="lines", name=str(y),
            line=dict(color=HIGHLIGHT_COLOR if is_hi else colors[y], width=3.5 if is_hi else 1.3),
            opacity=1.0 if is_hi else 0.85,
            customdata=custom, hovertemplate=hover,
            legendrank=1 if is_hi else 1000 - y,
        ))
        if is_hi and not d.empty:
            last = d.iloc[-1]
            x_last = x.iloc[-1]
            x_last = x_last.strftime("%Y-%m-%d") if hasattr(x_last, "strftime") else int(x_last)
            fig.add_annotation(x=x_last, y=float(last["Spread"]), text=f"<b>{y}</b>: {last['Spread']:.2f}",
                               showarrow=True, arrowhead=0, ax=30, ay=-24,
                               font=dict(color=HIGHLIGHT_COLOR, size=12))

    fig.add_hline(y=0, line=dict(color="#9ca3af", width=1))
    if x_axis == "SeasonalDate":
        xaxis = dict(title=None, tickformat="%b", dtick="M1", ticklabelmode="period",
                     rangeslider=dict(visible=range_slider, thickness=0.06))
    else:
        xaxis = dict(title="Days to Leg 1 delivery month", autorange="reversed",
                     rangeslider=dict(visible=range_slider, thickness=0.06))
    fig.update_layout(
        title=dict(text=title, x=0.01),
        template="plotly_white", height=height, hovermode="closest",
        xaxis=xaxis,
        yaxis=dict(title=f"Spread (Leg 1 - Leg 2){unit_sfx}", gridcolor=GRID_COLOR, zeroline=False),
        legend=dict(title="Crop year", orientation="v", x=1.01, y=1, font=dict(size=11)),
        margin=dict(l=60, r=20, t=60, b=40),
    )
    fig.update_xaxes(gridcolor=GRID_COLOR, showspikes=True, spikemode="across",
                     spikethickness=1, spikecolor="#9ca3af")
    return fig


def plot_spaghetti_mpl(spread_df: pd.DataFrame, title: str | None = None,
                       highlight_year: int | None = None, show_average: bool = True,
                       avg_years: int = 5, ax=None, units: str = ""):
    """Static Matplotlib version of plot_spaghetti (x = SeasonalDate)."""
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    if spread_df.empty:
        raise ValueError("Spread frame is empty: no overlapping dates for this pair.")
    label = spread_df["Label"].iloc[0]
    hi = _resolve_highlight(spread_df, highlight_year)
    years = sorted(spread_df["CropYear"].unique())
    history = [y for y in years if y != hi]
    colors = dict(zip(history, _history_colors(len(history))))

    if ax is None:
        _, ax = plt.subplots(figsize=(12, 6))
    for y in history:
        d = spread_df[spread_df["CropYear"] == y]
        r, g, b = (int(v) / 255 for v in colors[y][4:-1].split(","))
        ax.plot(d["SeasonalDate"], d["Spread"], color=(r, g, b), lw=1, label=str(y))
    if show_average:
        stats = seasonal_stats(spread_df, last_n=avg_years)
        if not stats.empty:
            ax.fill_between(stats["SeasonalDate"], stats["Min"], stats["Max"],
                            color="#64748b", alpha=0.12, lw=0)
            ax.plot(stats["SeasonalDate"], stats["Mean"], color=AVERAGE_COLOR, lw=2, ls="--",
                    label=f"Avg {stats['Years'].iloc[0]}")
    if hi is not None:
        d = spread_df[spread_df["CropYear"] == hi]
        ax.plot(d["SeasonalDate"], d["Spread"], color=HIGHLIGHT_COLOR, lw=3, label=str(hi))
    ax.axhline(0, color="#9ca3af", lw=1)
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
    ax.grid(axis="y", color=GRID_COLOR)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.set_title(title or f"{label} seasonal spread", loc="left")
    ax.set_ylabel(f"Spread (Leg 1 - Leg 2){' ' + units if units else ''}")
    ax.legend(title="Crop year", bbox_to_anchor=(1.01, 1), loc="upper left", fontsize=8, frameon=False)
    return ax
