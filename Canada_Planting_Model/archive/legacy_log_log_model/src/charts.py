"""Calibration charts (static PNG, matplotlib).

Run colors are the reference categorical slots 1-3 (blue, orange, aqua) in fixed order, each with
its own marker so identity never rests on color alone. Inverted fits are drawn as hatched grey.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import settings as S  # noqa: E402

RUN_STYLE = {
    "RUN1": {"color": "#2a78d6", "marker": "o"},
    "RUN2": {"color": "#eb6834", "marker": "s"},
    "RUN3": {"color": "#1baf7a", "marker": "^"},
}
INK, INK_2, GRID, SURFACE, MUTED = "#0b0b0b", "#52514e", "#e6e5e1", "#fcfcfb", "#b9b8b2"
PAIRS = [f"{c}/{a}" for c, a in S.PAIRS]


def _style(ax) -> None:
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=8)
    ax.grid(True, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def r2_by_window(fits: pd.DataFrame, selection: pd.DataFrame, path) -> None:
    """R^2 of the calibrated fit by pair and run; one panel per province; winners outlined."""
    provs = list(S.PROVINCES)
    fig, axes = plt.subplots(1, len(provs), figsize=(5.4 * len(provs), 4.4), sharey=True, facecolor=SURFACE)
    width = 0.26
    for ax, prov in zip(axes, provs):
        _style(ax)
        ax.grid(axis="x", visible=False)
        sel = selection[selection["province"] == prov].set_index("pair")
        for r_i, (run, st) in enumerate(RUN_STYLE.items()):
            for p_i, pair in enumerate(PAIRS):
                basis = sel.loc[pair, "basis"] if pair in sel.index else "common"
                sample = "own" if basis == "own" else "common"
                row = fits[(fits["province"] == prov) & (fits["pair"] == pair) & (fits["run"] == run)
                           & (fits["sample"] == sample)]
                if row.empty or row.iloc[0]["status"] not in ("OK", "LOW_N"):
                    continue
                r = row.iloc[0]
                x = p_i + (r_i - 1) * (width + 0.02)
                inverted = r["flag"] == "INVALID_INVERTED_SLOPE"
                h = max(r["r2_bounded"], 0.0)
                winner = pair in sel.index and sel.loc[pair, "winning_run"] == run
                ax.bar(x, h if not inverted else 0.03, width=width,
                       color=MUTED if inverted else st["color"], hatch="///" if inverted else None,
                       edgecolor=INK if winner else SURFACE, linewidth=1.6 if winner else 2)
        for p_i, pair in enumerate(PAIRS):
            if pair not in sel.index or sel.loc[pair, "basis"] in ("not estimable", S.EXCLUDED_STATUS):
                ax.text(p_i, 0.02, "n/a", ha="center", fontsize=7, color=INK_2, rotation=90)
        ax.set_xticks(range(len(PAIRS)))
        ax.set_xticklabels([p.replace("/", "/\n") for p in PAIRS], fontsize=7, color=INK_2)
        ax.set_ylim(0, 1)
        ax.set_title(S.PROVINCES[prov], fontsize=10, color=INK)
    axes[0].set_ylabel("R² of calibrated fit", fontsize=9, color=INK_2)
    handles = [plt.Rectangle((0, 0), 1, 1, color=st["color"], label=S.WINDOW_LABELS[r]) for r, st in RUN_STYLE.items()]
    handles.append(plt.Rectangle((0, 0), 1, 1, facecolor=MUTED, hatch="///", edgecolor=SURFACE,
                                 label="INVALID_INVERTED_SLOPE (excluded)"))
    handles.append(plt.Rectangle((0, 0), 1, 1, facecolor="none", edgecolor=INK, linewidth=1.6, label="Winning window"))
    fig.legend(handles=handles, loc="upper center", ncol=5, frameon=False, fontsize=8.5,
               bbox_to_anchor=(0.5, 1.08), labelcolor=INK)
    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)


def fit_scatter(ratios: pd.DataFrame, fits: pd.DataFrame, path) -> None:
    """Small multiples (province x pair): log margin deviation vs log acreage change.
    Solid line = calibrated fit; dashed = bounded prior (beta 0.10) for inverted fits."""
    provs = list(S.PROVINCES)
    fig, axes = plt.subplots(len(provs), len(PAIRS), figsize=(3.0 * len(PAIRS), 3.0 * len(provs)),
                             squeeze=False, facecolor=SURFACE)
    for i, prov in enumerate(provs):
        for k, pair in enumerate(PAIRS):
            ax = axes[i][k]
            _style(ax)
            drawn = False
            for run, st in RUN_STYLE.items():
                d = ratios[(ratios["province"] == prov) & (ratios["pair"] == pair) & (ratios["run"] == run)]
                d = d.dropna(subset=["x_dev", "y_dlog_acres"])
                if d.empty:
                    continue
                drawn = True
                ax.scatter(d["x_dev"], d["y_dlog_acres"], s=28, color=st["color"], marker=st["marker"],
                           edgecolor=SURFACE, linewidth=1.5, zorder=3)
                f = fits[(fits["province"] == prov) & (fits["pair"] == pair) & (fits["run"] == run)
                         & (fits["sample"] == "own")]
                if not f.empty and f.iloc[0]["status"] in ("OK", "LOW_N"):
                    r = f.iloc[0]
                    xs = np.linspace(d["x_dev"].min(), d["x_dev"].max(), 20)
                    ax.plot(xs, r["alpha0_bounded"] + r["beta_bounded"] * xs, color=st["color"], linewidth=2,
                            linestyle="--" if r["flag"] == "INVALID_INVERTED_SLOPE" else "-")
            if not drawn:
                ax.text(0.5, 0.5, "not estimable", ha="center", va="center", color=INK_2, fontsize=8,
                        transform=ax.transAxes)
                ax.set_xticks([]); ax.set_yticks([])
            if i == 0:
                ax.set_title(pair.replace("/", " / "), fontsize=9, color=INK)
            if k == 0:
                ax.set_ylabel(f"{prov}\nln(Acres t / t-1)", fontsize=8, color=INK_2)
            if i == len(provs) - 1:
                ax.set_xlabel("ln(R t / R-bar t-1)", fontsize=8, color=INK_2)
    handles = [plt.Line2D([], [], color=st["color"], marker=st["marker"], linewidth=2, markersize=6,
                          label=S.WINDOW_LABELS[r]) for r, st in RUN_STYLE.items()]
    handles.append(plt.Line2D([], [], color=INK_2, linestyle="--", linewidth=2, label="Bounded prior (inverted fit)"))
    fig.legend(handles=handles, loc="upper center", ncol=4, frameon=False, fontsize=9,
               bbox_to_anchor=(0.5, 1.0), labelcolor=INK)
    fig.suptitle("Calibrated log-log acreage response by province, pair and pricing window",
                 y=1.04, fontsize=11, color=INK)
    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)
