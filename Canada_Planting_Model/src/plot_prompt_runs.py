"""Daily prompt-price charts for Canola and CWRS, Run 1-3, planting years 2019-2025.

prices_prompt_windows.csv holds one averaged price per window, so the daily series is rebuilt
here with the same prompt rule (prompt_prices.prompt_zone_days, spec 4.1): one bid per crop x zone
x day, provincial daily mean over quoting zones. The chart plots the equal-weight mean of the AB,
SK and MB daily prices. Each run is one panel; x = days since the window opened; one line per
planting year. The dashed line marks the 1st of the month, where the prompt bid rolls from the
expired start month to the next delivery month.

    python src/plot_prompt_runs.py

Writes output/canola_prompt_runs_2019_2025.png, output/cwrs_prompt_runs_2019_2025.png and
output/reports/prices_prompt_daily.csv.
"""

from __future__ import annotations

from datetime import date

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

import prompt_prices as pp  # noqa: E402
import settings as S  # noqa: E402

YEARS = tuple(range(2019, 2026))
CROPS = {"CANOLA": ("Canola", "canola"), "CWRS": ("CWRS Wheat", "cwrs")}

# Reference categorical palette, slots 1-7 in fixed order (one per planting year).
YEAR_COLORS = dict(zip(YEARS, ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7"]))
INK, INK_2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e6e5e1", "#fcfcfb"


def daily_prompt_prices() -> pd.DataFrame:
    """Provincial daily prompt price per crop, run and planting year (CANOLA and CWRS)."""
    con = pp._connect()
    frames = []
    try:
        for year in YEARS:
            for run in S.WINDOWS:
                zd = pp.prompt_zone_days(con, year, run)
                zd = zd[zd["crop_symbol"].isin(CROPS)]
                if zd.empty:
                    continue
                daily = (zd.groupby(["crop_symbol", "province", "price_date"])
                           .agg(price=("price_cad_bu", "mean"), zones=("pdq_zone", "count"),
                                primary=("is_primary", "mean"))
                           .reset_index())
                start = pp.window_bounds(year, run)[0]
                daily["year"], daily["run"] = year, run
                daily["day"] = (pd.to_datetime(daily["price_date"]) - pd.Timestamp(start)).dt.days
                frames.append(daily)
    finally:
        con.close()
    return pd.concat(frames, ignore_index=True)


def plot_crop(daily: pd.DataFrame, symbol: str, path) -> None:
    label, _ = CROPS[symbol]
    d = daily[daily["crop_symbol"] == symbol]
    prairie = d.groupby(["run", "year", "day"], as_index=False)["price"].mean()   # AB/SK/MB equal-weight mean

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.6), sharey=True, facecolor=SURFACE)
    for ax, run in zip(axes, S.WINDOWS):
        ax.set_facecolor(SURFACE)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(GRID)
        ax.grid(True, color=GRID, linewidth=0.6)
        ax.set_axisbelow(True)
        ax.tick_params(colors=INK_2, labelsize=8)

        for year in YEARS:
            s = prairie[(prairie["run"] == run) & (prairie["year"] == year)].sort_values("day")
            if not s.empty:
                ax.plot(s["day"], s["price"], color=YEAR_COLORS[year], linewidth=2, label=str(year))

        start = pp.window_bounds(2024, run)[0]
        roll_day = (date(start.year + (start.month == 12), start.month % 12 + 1, 1) - start).days
        ax.axvline(roll_day, color=INK_2, linestyle="--", linewidth=1)
        ax.text(roll_day + 0.4, 0.98, "prompt month rolls", transform=ax.get_xaxis_transform(),
                fontsize=7, color=INK_2, va="top")
        ax.set_title(S.WINDOW_LABELS[run], fontsize=10, color=INK)
        ax.set_xlabel("Days since window opened", fontsize=8, color=INK_2)
        ax.set_xlim(0, 31)
    axes[0].set_ylabel("Prompt cash bid, $/bu (AB/SK/MB mean)", fontsize=9, color=INK_2)

    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, [f"{l} planting year" if i == 0 else l for i, l in enumerate(labels)],
               loc="upper center", ncol=len(YEARS), frameon=False, fontsize=8.5, bbox_to_anchor=(0.5, 1.02),
               labelcolor=INK)
    fig.suptitle(f"{label} - daily PDQ prompt bids by pricing window, planting years 2019-2025",
                 y=1.09, fontsize=11, color=INK)
    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)


def main() -> int:
    daily = daily_prompt_prices()
    S.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    daily.to_csv(S.REPORTS_DIR / "prices_prompt_daily.csv", index=False)
    for symbol, (_, stem) in CROPS.items():
        path = S.OUTPUT_DIR / f"{stem}_prompt_runs_2019_2025.png"
        plot_crop(daily, symbol, path)
        print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
