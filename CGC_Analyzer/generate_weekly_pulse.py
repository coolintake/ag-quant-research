#!/usr/bin/env python3
"""
generate_weekly_pulse.py
=========================
Automated "Canadian Grain Handling Weekly" report generator (internal
pipeline nickname: Weekly Pulse).

Pipeline
--------
1. Data Extractor & Metrics Processor  -> WeeklyPulseEngine (wraps CGCAnalytics)
2. Objective Rules Engine              -> RulesEngine
3. Chart Generator (Matplotlib)        -> build_export_pacing_chart() (Ghost feature image)
4. Commentary (LLM or rule-based)      -> CommentaryWriter (3-bullet Summary, Lingo_CGCWeeklyReport skill)
5. HTML render (Jinja2 template)       -> render_report_html()
6. Ghost Admin API staging (draft)     -> GhostPublisher

Run:
    python generate_weekly_pulse.py                  # full pipeline, stage draft to Ghost
    python generate_weekly_pulse.py --no-publish      # build HTML + chart locally only
    python generate_weekly_pulse.py --no-llm          # force rule-based commentary
    python generate_weekly_pulse.py --week 24         # force a specific grain_week (testing)
    python generate_weekly_pulse.py --force-refresh   # force a full GSW/capacity re-download

Configuration is via environment variables (see CONFIG section below) so no
secrets are hard-coded in source. A `.env` file next to this script is loaded
automatically if `python-dotenv` is installed (optional).

DATA SOURCE — SINGLE SOURCE OF TRUTH
-------------------------------------
All GSW ingestion, schema normalization, and metric math (deliveries,
exports/outflow, stocks, capacity utilization) come from the existing
CGC_Analyzer backend — `core/` (`cgc_engine` / `ingestion` / `cgc_reports`) —
via the `CGCAnalytics` facade, exactly the way `run_report.py` and `app.py`
already use it. This script does NOT download or parse raw GSW CSVs itself,
and does NOT re-implement any of the deliveries/exports/stocks formulas —
it only calls into `CGCAnalytics` and reshapes what comes back for the
report layout, the rules engine, the chart, and Ghost publishing.

The one exception is weekly Terminal Exports volume split by West Coast vs.
East/Interior region (Section 1 "Terminal Outflow" in the report) — no
existing `cgc_reports.py` function currently exposes an outflow-by-region
breakdown (only `stocks_by_node()` breaks stocks down by terminal region;
outflow/exports are only exposed at the national level via `.outflow`).
`_terminal_outflow_by_region()` below fills that one gap by reading the same
already-cleaned/standardized DataFrame CGCAnalytics itself holds (via its
`_require_data()` accessor — the same guarded accessor the facade's own
methods use internally) rather than re-downloading or re-parsing anything.
If this split becomes useful elsewhere on the dashboard, promoting it into
`cgc_engine.py`/`cgc_reports.py` as a first-class function would be a
reasonable follow-up so this script isn't the only caller of it.

Terminal "Capacity Util %" itself does NOT need this workaround — it comes
straight from `CGCAnalytics.get_regional_utilization_matrix()`, which already
computes stocks/capacity per terminal region using the licensed-capacity
workbook via the exact same `CapacityLoader`/`REGION_STATION_MAP` used
everywhere else in the project.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(
    level=os.environ.get("WEEKLY_PULSE_LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("weekly_pulse")

# ══════════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════════

SCRIPT_DIR = Path(__file__).resolve().parent

# The shared data layer is the `core` package next to this script; make it
# importable regardless of the current working directory.
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from core.cgc_engine import (  # noqa: E402  (path setup must happen first)
    DEFAULT_CROP_YEARS,
    DEFAULT_CURRENT_YEAR,
    TERMINAL_LOCATIONS,
    to_raw_grain_name,
)
from core.cgc_reports import CGCAnalytics  # noqa: E402

# Same self-locating-paths convention as run_report.py.
DATA_DIR = SCRIPT_DIR / "data"
GSW_DATA_DIR = Path(os.environ.get("CGC_DATA_DIR", DATA_DIR / "gsw_data"))


def _resolve_capacity_path(here: Path) -> Path:
    """Find CGC_Capacity.(xlsb|xlsx|xls) in data/, preferring
    .xlsb but accepting whichever extension actually exists — mirrors
    run_report.py's resolution exactly so both entry points find the same
    file without needing separate configuration."""
    for ext in (".xlsb", ".xlsx", ".xls"):
        candidate = here / f"CGC_Capacity{ext}"
        if candidate.exists():
            return candidate
    return here / "CGC_Capacity.xlsb"


CAPACITY_PATH = Path(os.environ.get("CGC_CAPACITY_FILE", _resolve_capacity_path(DATA_DIR)))

OUTPUT_DIR = SCRIPT_DIR / "Outputs" / "weekly_pulse"
CHART_PATH = OUTPUT_DIR / "export_pacing_chart.png"
HTML_OUTPUT_PATH = OUTPUT_DIR / "weekly_pulse_report.html"
JSON_PAYLOAD_PATH = OUTPUT_DIR / "weekly_pulse_payload.json"
TEMPLATE_DIR = SCRIPT_DIR / "templates"
TEMPLATE_NAME = "weekly_pulse_template.html.j2"

# Ghost Admin API
GHOST_API_URL = os.environ.get("GHOST_API_URL", "").rstrip("/")
GHOST_ADMIN_API_KEY = os.environ.get("GHOST_ADMIN_API_KEY", "")  # "id:secret"

# LLM commentary (optional)
USE_LLM_DEFAULT = os.environ.get("WEEKLY_PULSE_USE_LLM", "0") == "1"
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

FOOTER_URL = "chilmeranresearch.com"

# Publication title -- single source of truth for the masthead (template),
# the Ghost post title, and anywhere else the report's name is displayed.
PUBLICATION_TITLE = "Canadian Grain Handling Weekly"

# Chart watermark (Matplotlib, lower-right margin). The template's footer
# text ("Chilmeran Research | Grain Market Intelligence | chilmeranresearch.com")
# is written directly in weekly_pulse_template.html.j2 so its domain can stay
# a clickable link -- FOOTER_URL above supplies that link's href/text.
WATERMARK_TEXT = "Chilmeran Research • chilmeranresearch.com"

# ── Commodities covered by this report ──────────────────────────────────
# Canonical display names as cgc_engine understands them (see
# to_raw_grain_name/to_display_grain_name). Standardized to "Amber Durum"
# (not "Durum") to match the raw CGC/GSW grain label directly, per current
# project convention — to_raw_grain_name("Amber Durum") is an identity
# passthrough (only the string "Durum" has a non-identity mapping in
# GRAIN_NAME_TO_RAW), so this still resolves correctly against the engine.
# WEEKLY_PULSE_LABELS overrides just the one name the report wants written
# differently from the engine's own canonical name ("Wheat" -> "Wheat
# (excl. Durum)", since GSW's plain "Wheat" grain already excludes durum).
WEEKLY_PULSE_COMMODITIES: list[str] = [
    "Wheat", "Amber Durum", "Barley", "Canola", "Soybeans", "Corn", "Lentils", "Peas",
]
WEEKLY_PULSE_LABELS: dict[str, str] = {"Wheat": "Wheat (excl. Durum)"}

# 5-year lookback for both deliveries and exports pacing, per the report
# spec. Passed explicitly to CGCAnalytics methods that default to 3 years
# (get_producer_deliveries_summary / cumulative_pacing_table) rather than
# hardcoding a separate calculation — the underlying field names in their
# return values still say "avg3yr_*" (that's just the field's fixed name),
# but the number itself reflects this 5-year window since it's what we pass.
PACING_LOOKBACK_YEARS = 5

# Terminal region split for the Section 1 "Terminal Outflow" macro line.
# Reuses cgc_engine.TERMINAL_LOCATIONS (the canonical 6-region GSW list)
# rather than redefining it, so this can never silently drift from the rest
# of the project if that list is ever extended.
WEST_COAST_REGIONS: list[str] = ["Vancouver", "Prince Rupert"]
EAST_INTERIOR_REGIONS: list[str] = [r for r in TERMINAL_LOCATIONS if r not in WEST_COAST_REGIONS]

# ── Rules engine thresholds ─────────────────────────────────────────────
RULE_THRESHOLDS = {
    "surging_wow_pct": 20.0,
    "slowing_wow_pct": -20.0,
    "strong_export_pace_pct": 25.0,
    "weak_export_pace_pct": -25.0,
    "tightening_stocks_yoy_pct": -20.0,
    "building_stocks_yoy_pct": 20.0,
    "high_utilization_pct": 80.0,
}

# Early crop-year percentages (deliveries/exports YTD vs LY) can swing wildly
# because the prior-year base itself is tiny in the season's first weeks.
# weeks 1 through this constant get an asterisk on the Export YTD % column
# and a footnote explaining why; both disappear from week 11 onward.
EARLY_SEASON_MAX_WEEK: int = 10


# ══════════════════════════════════════════════════════════════════════════
# 1. DATA EXTRACTOR & METRICS PROCESSOR
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class CommodityMetrics:
    display_name: str
    raw_name: Optional[str]
    week_number: Optional[int] = None
    crop_year: Optional[str] = None

    weekly_deliveries_kt: Optional[float] = None
    deliveries_wow_pct: Optional[float] = None
    ytd_deliveries_mmt: Optional[float] = None
    deliveries_yoy_pct: Optional[float] = None
    deliveries_vs_5yr_pct: Optional[float] = None

    weekly_exports_kt: Optional[float] = None
    ytd_exports_kt: Optional[float] = None
    ytd_exports_mmt: Optional[float] = None
    exports_yoy_pct: Optional[float] = None
    exports_vs_5yr_pct: Optional[float] = None

    # YTD exports split by terminal corridor (Section 3 "YTD Exports by
    # Corridor" table). NOTE: these come only from the Terminal Exports
    # worksheet, which is the only part of the export figure that carries a
    # region -- the small "direct rail" component from Primary Shipment
    # Distribution/Export Destinations (included in exports_yoy_pct/
    # ytd_exports_kt above) has no region breakdown in GSW's source data, so
    # west_export_ytd_kt + east_export_ytd_kt will run slightly BELOW
    # ytd_exports_kt, not equal to it. This is a genuine data-model
    # limitation, not a bug -- flagged here and in the corridor table's note.
    west_export_ytd_kt: Optional[float] = None
    west_export_yoy_pct: Optional[float] = None
    east_export_ytd_kt: Optional[float] = None
    east_export_yoy_pct: Optional[float] = None

    stocks_kt: Optional[float] = None
    stocks_yoy_pct: Optional[float] = None

    # Same-week last-year volumes behind exports_yoy_pct / stocks_yoy_pct --
    # the Summary quotes these instead of % changes early in the crop year.
    ly_ytd_exports_kt: Optional[float] = None
    stocks_ly_kt: Optional[float] = None

    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _pct_change(new: Optional[float], old: Optional[float]) -> Optional[float]:
    if new is None or old is None:
        return None
    if isinstance(old, float) and (np.isnan(old) or old == 0):
        return None
    if isinstance(new, float) and np.isnan(new):
        return None
    return (new - old) / old * 100.0


def _safe_sum(series: pd.Series) -> Optional[float]:
    return float(series.sum()) if not series.empty else None


def _terminal_outflow_by_region(gsw_df: pd.DataFrame, raw_grains: list[str], regions: list[str],
                                 crop_year: str, week: Optional[int], period: str = "Current Week") -> float:
    """Terminal Exports volume for `raw_grains`, summed over `regions`, for
    the given `period` -- "Current Week" (weekly, the original use of this
    function for the macro snapshot) or "Crop Year" (cumulative YTD, used by
    the Section 3 corridor table). See module docstring: this is the one
    metric not currently exposed by cgc_reports.py's facade (which only
    breaks STOCKS down by terminal region, not outflow/exports), so it reads
    directly from the same standardized DataFrame CGCAnalytics itself holds
    rather than re-parsing anything.
    """
    mask = (
        gsw_df["grain"].isin(raw_grains)
        & (gsw_df["worksheet"] == "Terminal Exports")
        & (gsw_df["metric"] == "Exports")
        & (gsw_df["period"] == period)
        & (gsw_df["crop_year"] == crop_year)
        & (gsw_df["region"].isin(regions))
    )
    if week is not None:
        mask &= gsw_df["grain_week"] == week
    return float(gsw_df.loc[mask, "Ktonnes"].sum())


class WeeklyPulseEngine:
    """Computes all Weekly Pulse metrics by calling into the existing
    CGCAnalytics facade — this class does no GSW parsing or formula math of
    its own; it only selects, diffs, and reshapes what CGCAnalytics returns.
    """

    def __init__(self, analytics: CGCAnalytics):
        self.analytics = analytics
        self.current_crop_year = analytics._resolve_crop_year(None)
        all_years = sorted(analytics._require_data()["crop_year"].dropna().unique())
        idx = all_years.index(self.current_crop_year) if self.current_crop_year in all_years else len(all_years)
        self.last_crop_year = all_years[idx - 1] if idx >= 1 else None
        self.five_yr_pool = all_years[max(0, idx - 5):idx]

    # ── helpers ─────────────────────────────────────────────────────────
    def _current_week(self) -> Optional[int]:
        return self.analytics._resolve_grain_week(self.current_crop_year, None)

    @staticmethod
    def _prior_week(week: Optional[int]) -> Optional[int]:
        """GSW grain_weeks are sequential within a crop year (no gaps), so
        the prior week is simply week - 1 (None at the season's first week)."""
        return week - 1 if week and week > 1 else None

    def _deliveries_total_row(self, display_name: str, crop_year: str, week: Optional[int]) -> pd.Series:
        """The 'Total Producer Deliveries' row (Western Canada Primary +
        Process National) from CGCAnalytics.get_producer_deliveries_summary,
        for a single commodity/week. Empty-safe: returns a Series of NaNs
        if week is None or the summary comes back empty for it."""
        empty = pd.Series({
            "current_week_ktonnes": np.nan, "ytd_ktonnes": np.nan,
            "ytd_yoy_pct": np.nan, "ytd_avg3yr_delta_pct": np.nan,
        })
        if week is None:
            return empty
        summary = self.analytics.get_producer_deliveries_summary(
            commodities=[display_name], crop_year=crop_year, grain_week=week,
            lookback_years=PACING_LOOKBACK_YEARS,
        )
        row = summary[summary["region"] == "Total Producer Deliveries"]
        return row.iloc[0] if not row.empty else empty

    def _outflow_slice(self, raw_grain: str, crop_year: str, week: Optional[int]) -> pd.DataFrame:
        d = self.analytics.outflow  # ['grain','crop_year','grain_week','cum_ktonnes','weekly_outflow_ktonnes']
        mask = (d["grain"] == raw_grain) & (d["crop_year"] == crop_year)
        if week is not None:
            mask &= d["grain_week"] == week
        return d.loc[mask]

    def _stocks_total(self, raw_grain: str, crop_year: str, week: Optional[int]) -> Optional[float]:
        d = self.analytics.stocks  # ['grain','crop_year','grain_week','segment','stocks_ktonnes']
        mask = (d["grain"] == raw_grain) & (d["crop_year"] == crop_year)
        if week is not None:
            mask &= d["grain_week"] == week
        sub = d.loc[mask]
        return _safe_sum(sub["stocks_ktonnes"]) if not sub.empty else None

    def _corridor_ytd(self, raw_grain: str, crop_year: str, week: Optional[int]) -> tuple[float, float]:
        """(west_ytd_kt, east_ytd_kt): cumulative YTD Terminal Exports for one
        commodity, split West Coast vs. East/Interior. See CommodityMetrics'
        docstring note -- Terminal Exports only, so this excludes the small
        direct-rail export component that has no region attribution."""
        gsw_df = self.analytics._require_data()
        west = _terminal_outflow_by_region(gsw_df, [raw_grain], WEST_COAST_REGIONS, crop_year, week, period="Crop Year")
        east = _terminal_outflow_by_region(gsw_df, [raw_grain], EAST_INTERIOR_REGIONS, crop_year, week, period="Crop Year")
        return west, east

    # ── per-commodity metrics ───────────────────────────────────────────
    def compute_commodity(self, display_name: str, week: Optional[int] = None) -> CommodityMetrics:
        raw = to_raw_grain_name(display_name)
        report_label = WEEKLY_PULSE_LABELS.get(display_name, display_name)
        m = CommodityMetrics(display_name=report_label, raw_name=raw)

        cy, ly = self.current_crop_year, self.last_crop_year
        wk = week if week is not None else self._current_week()
        prior_wk = self._prior_week(wk)
        m.week_number, m.crop_year = wk, cy

        # -- Deliveries (via CGCAnalytics.get_producer_deliveries_summary) --
        now_row = self._deliveries_total_row(display_name, cy, wk)
        prior_row = self._deliveries_total_row(display_name, cy, prior_wk)

        m.weekly_deliveries_kt = float(now_row["current_week_ktonnes"]) if pd.notna(now_row["current_week_ktonnes"]) else None
        prior_week_kt = float(prior_row["current_week_ktonnes"]) if pd.notna(prior_row["current_week_ktonnes"]) else None
        m.deliveries_wow_pct = _pct_change(m.weekly_deliveries_kt, prior_week_kt)

        ytd_deliv = float(now_row["ytd_ktonnes"]) if pd.notna(now_row["ytd_ktonnes"]) else None
        m.ytd_deliveries_mmt = ytd_deliv / 1000.0 if ytd_deliv is not None else None
        m.deliveries_yoy_pct = float(now_row["ytd_yoy_pct"]) if pd.notna(now_row["ytd_yoy_pct"]) else None
        m.deliveries_vs_5yr_pct = float(now_row["ytd_avg3yr_delta_pct"]) if pd.notna(now_row["ytd_avg3yr_delta_pct"]) else None

        # -- Exports (via CGCAnalytics.outflow, already-computed by cgc_engine.weekly_outflow) --
        cur = self._outflow_slice(raw, cy, wk)
        m.weekly_exports_kt = _safe_sum(cur["weekly_outflow_ktonnes"]) if not cur.empty else None
        ytd_exp = _safe_sum(cur["cum_ktonnes"]) if not cur.empty else None
        m.ytd_exports_kt = ytd_exp
        m.ytd_exports_mmt = ytd_exp / 1000.0 if ytd_exp is not None else None

        ly_cur = self._outflow_slice(raw, ly, wk) if ly else pd.DataFrame()
        ly_ytd_exp = _safe_sum(ly_cur["cum_ktonnes"]) if not ly_cur.empty else None
        m.ly_ytd_exports_kt = ly_ytd_exp
        m.exports_yoy_pct = _pct_change(ytd_exp, ly_ytd_exp)

        five_yr_vals = []
        for yr in self.five_yr_pool:
            yr_cur = self._outflow_slice(raw, yr, wk)
            if not yr_cur.empty:
                v = _safe_sum(yr_cur["cum_ktonnes"])
                if v is not None and v != 0:
                    five_yr_vals.append(v)
        five_yr_avg_exp = float(np.mean(five_yr_vals)) if five_yr_vals else None
        m.exports_vs_5yr_pct = _pct_change(ytd_exp, five_yr_avg_exp)

        # -- YTD Exports by Corridor (Section 3 table) --
        m.west_export_ytd_kt, m.east_export_ytd_kt = self._corridor_ytd(raw, cy, wk)
        if ly:
            ly_west_ytd, ly_east_ytd = self._corridor_ytd(raw, ly, wk)
            m.west_export_yoy_pct = _pct_change(m.west_export_ytd_kt, ly_west_ytd)
            m.east_export_yoy_pct = _pct_change(m.east_export_ytd_kt, ly_east_ytd)

        # -- Stocks (via CGCAnalytics.stocks, already-computed by cgc_engine.stocks_slice) --
        stocks_now = self._stocks_total(raw, cy, wk)
        stocks_ly = self._stocks_total(raw, ly, wk) if ly else None
        m.stocks_kt = stocks_now
        m.stocks_ly_kt = stocks_ly
        m.stocks_yoy_pct = _pct_change(stocks_now, stocks_ly)

        return m

    def compute_all(self, week: Optional[int] = None) -> list[CommodityMetrics]:
        return [self.compute_commodity(name, week) for name in WEEKLY_PULSE_COMMODITIES]

    def five_year_export_series(self, week: Optional[int] = None) -> pd.DataFrame:
        """Table of CY / LY / 5-Yr-Avg YTD export volumes for all 8
        commodities (feeds the export pacing chart)."""
        wk = week if week is not None else self._current_week()
        rows = []
        for display_name in WEEKLY_PULSE_COMMODITIES:
            raw = to_raw_grain_name(display_name)
            report_label = WEEKLY_PULSE_LABELS.get(display_name, display_name)
            cy_cur = self._outflow_slice(raw, self.current_crop_year, wk)
            cy_val = _safe_sum(cy_cur["cum_ktonnes"]) if not cy_cur.empty else np.nan

            ly_val = np.nan
            if self.last_crop_year:
                ly_cur = self._outflow_slice(raw, self.last_crop_year, wk)
                ly_val = _safe_sum(ly_cur["cum_ktonnes"]) if not ly_cur.empty else np.nan

            five_yr_vals = []
            for yr in self.five_yr_pool:
                yr_cur = self._outflow_slice(raw, yr, wk)
                if not yr_cur.empty:
                    v = _safe_sum(yr_cur["cum_ktonnes"])
                    if v is not None and v != 0:
                        five_yr_vals.append(v)
            avg5 = float(np.mean(five_yr_vals)) if five_yr_vals else np.nan

            rows.append({"commodity": report_label, "cy": cy_val, "ly": ly_val, "avg5": avg5})
        return pd.DataFrame(rows)

    # ── macro / terminal aggregation ────────────────────────────────────
    def macro_snapshot(self, week: Optional[int] = None) -> dict:
        wk = week if week is not None else self._current_week()
        cy, ly = self.current_crop_year, self.last_crop_year
        prior_wk = self._prior_week(wk)
        raws = [to_raw_grain_name(c) for c in WEEKLY_PULSE_COMMODITIES]

        # -- Deliveries, aggregated across all 8 commodities in one call --
        now_row = self._deliveries_total_row_multi(WEEKLY_PULSE_COMMODITIES, cy, wk)
        prior_row = self._deliveries_total_row_multi(WEEKLY_PULSE_COMMODITIES, cy, prior_wk)

        weekly_deliv = float(now_row["current_week_ktonnes"]) if pd.notna(now_row["current_week_ktonnes"]) else None
        prior_weekly_deliv = float(prior_row["current_week_ktonnes"]) if pd.notna(prior_row["current_week_ktonnes"]) else None
        ytd_deliv = float(now_row["ytd_ktonnes"]) if pd.notna(now_row["ytd_ktonnes"]) else None

        # -- Terminal outflow volumes West Coast vs. East/Interior --
        gsw_df = self.analytics._require_data()
        west_kt = _terminal_outflow_by_region(gsw_df, raws, WEST_COAST_REGIONS, cy, wk)
        east_kt = _terminal_outflow_by_region(gsw_df, raws, EAST_INTERIOR_REGIONS, cy, wk)

        # -- Terminal Capacity Util % (stocks / licensed capacity) --
        # BUGFIX: this previously summed `capacity_ktonnes` across the
        # per-(grain, segment) rows returned by
        # get_regional_utilization_matrix(). That matrix left-joins each
        # terminal's SHARED physical capacity onto every commodity's stock
        # row (correct for capacity_utilization()'s own per-commodity
        # purpose), but summing capacity across those rows re-counts the
        # SAME physical silo capacity once per commodity present at that
        # segment -- inflating the denominator by roughly however many of
        # our 8 tracked commodities have stock there. That's exactly the
        # ~5-7% vs. true ~38.5% discrepancy: true_util / (# commodities
        # with stock at that segment).
        #
        # Fixed by sourcing capacity from `capacity_by_segment` directly --
        # ONE row per physical segment, no grain dimension to accidentally
        # multiply-count -- and by summing terminal STOCKS ACROSS ALL
        # COMMODITIES in the dataset (not just this report's 8 tracked
        # ones), since "how full is Vancouver" is a physical-infrastructure
        # question independent of which grains Weekly Pulse happens to
        # editorially cover -- grain from ANY commodity sitting there still
        # occupies real physical space.
        #
        # NOTE this intentionally differs in scope from `west_coast_kt` /
        # `east_interior_kt` just above, which stay restricted to the 8
        # tracked commodities on purpose -- that's "how much of OUR covered
        # flow moved through the terminal this week," a different question
        # from "how full is the terminal, period." Flag if you actually
        # want outflow volume broadened to all commodities too, for
        # symmetry with this fix.
        all_stocks = self.analytics.stocks  # ['grain','crop_year','grain_week','segment','stocks_ktonnes'] -- ALL commodities
        stocks_now = all_stocks[(all_stocks["crop_year"] == cy) & (all_stocks["grain_week"] == wk)]
        capacity_by_segment = self.analytics.capacity_by_segment  # ['segment','capacity_ktonnes'] -- one row per segment

        def region_group_util(regions: list[str]) -> Optional[float]:
            stocks = stocks_now.loc[stocks_now["segment"].isin(regions), "stocks_ktonnes"].sum()
            capacity = capacity_by_segment.loc[capacity_by_segment["segment"].isin(regions), "capacity_ktonnes"].sum()
            return (stocks / capacity * 100.0) if capacity and capacity > 0 else None

        return {
            "week_number": wk,
            "crop_year": cy,
            "weekly_deliveries_kt": weekly_deliv,
            "deliveries_wow_pct": _pct_change(weekly_deliv, prior_weekly_deliv),
            "ytd_deliveries_mmt": ytd_deliv / 1000.0 if ytd_deliv is not None else None,
            "deliveries_yoy_pct": float(now_row["ytd_yoy_pct"]) if pd.notna(now_row["ytd_yoy_pct"]) else None,
            "deliveries_vs_5yr_pct": float(now_row["ytd_avg3yr_delta_pct"]) if pd.notna(now_row["ytd_avg3yr_delta_pct"]) else None,
            "terminal_outflow_total_kt": west_kt + east_kt,
            "west_coast_kt": west_kt,
            "west_coast_util_pct": region_group_util(WEST_COAST_REGIONS),
            "east_interior_kt": east_kt,
            "east_interior_util_pct": region_group_util(EAST_INTERIOR_REGIONS),
        }

    def _deliveries_total_row_multi(self, display_names: list[str], crop_year: str, week: Optional[int]) -> pd.Series:
        """Same as _deliveries_total_row but aggregated across several
        commodities in one call (used for the macro snapshot's all-8 total)."""
        empty = pd.Series({
            "current_week_ktonnes": np.nan, "ytd_ktonnes": np.nan,
            "ytd_yoy_pct": np.nan, "ytd_avg3yr_delta_pct": np.nan,
        })
        if week is None:
            return empty
        summary = self.analytics.get_producer_deliveries_summary(
            commodities=display_names, crop_year=crop_year, grain_week=week,
            lookback_years=PACING_LOOKBACK_YEARS,
        )
        row = summary[summary["region"] == "Total Producer Deliveries"]
        return row.iloc[0] if not row.empty else empty


# ══════════════════════════════════════════════════════════════════════════
# 2. OBJECTIVE RULES ENGINE
# ══════════════════════════════════════════════════════════════════════════

class RulesEngine:
    """Applies deterministic, threshold-based rules — no LLM judgement involved."""

    def __init__(self, thresholds: dict = None):
        self.t = thresholds or RULE_THRESHOLDS

    def tag_commodity(self, m: CommodityMetrics) -> list[str]:
        flags = []
        if m.deliveries_wow_pct is not None:
            if m.deliveries_wow_pct > self.t["surging_wow_pct"]:
                flags.append("SURGING_DELIVERIES")
            elif m.deliveries_wow_pct < self.t["slowing_wow_pct"]:
                flags.append("SLOWING_DELIVERIES")
        if m.exports_yoy_pct is not None:
            if m.exports_yoy_pct > self.t["strong_export_pace_pct"]:
                flags.append("STRONG_EXPORT_PACE")
            elif m.exports_yoy_pct < self.t["weak_export_pace_pct"]:
                flags.append("WEAK_EXPORT_PACE")
        if m.stocks_yoy_pct is not None:
            if m.stocks_yoy_pct < self.t["tightening_stocks_yoy_pct"]:
                flags.append("TIGHTENING_STOCKS")
            elif m.stocks_yoy_pct > self.t["building_stocks_yoy_pct"]:
                flags.append("BUILDING_STOCKS")
        m.flags = flags
        return flags

    def tag_macro(self, macro: dict) -> list[str]:
        flags = []
        for side, key in (("WEST_COAST", "west_coast_util_pct"), ("EAST_INTERIOR", "east_interior_util_pct")):
            val = macro.get(key)
            if val is not None and val > self.t["high_utilization_pct"]:
                flags.append(f"HIGH_UTILIZATION_{side}")
        return flags

    def build_payload(self, macro: dict, commodities: list[CommodityMetrics]) -> dict:
        for m in commodities:
            self.tag_commodity(m)
        macro_flags = self.tag_macro(macro)
        return {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "macro": {**macro, "flags": macro_flags},
            "commodities": [m.to_dict() for m in commodities],
            "thresholds": self.t,
        }


# ══════════════════════════════════════════════════════════════════════════
# 3. CHART GENERATOR
# ══════════════════════════════════════════════════════════════════════════

def build_export_pacing_chart(series_df: pd.DataFrame, out_path: Path = CHART_PATH) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker

    df = series_df.copy()
    n = len(df)
    x = np.arange(n)
    width = 0.26

    fig, ax = plt.subplots(figsize=(12, 6), dpi=150)
    fig.patch.set_facecolor("white")

    bars_avg = ax.bar(x - width, df["avg5"], width, label="5-Yr Avg", color="#E2E8F0")
    bars_ly = ax.bar(x, df["ly"], width, label="Last Year", color="#94A3B8")
    bars_cy = ax.bar(x + width, df["cy"], width, label="This Year (YTD)", color="#1F3B57",
                      hatch="//", edgecolor="#0F172A", linewidth=0.8)

    ax.set_title("YTD Cumulative Exports by Commodity", fontsize=15, fontweight="bold", pad=14,
                  color="#1F3B57")
    ax.set_ylabel("Cumulative Exports (KMT)", fontsize=11)
    ax.set_xticks(x)
    ax.set_xticklabels(df["commodity"], rotation=20, ha="right", fontsize=10)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    ax.grid(axis="y", linestyle=":", alpha=0.4)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(frameon=False, fontsize=10, loc="upper right")

    plt.tight_layout()

    # Subtle branding watermark, lower-right margin -- added after
    # tight_layout() so it sits in the figure's own margin rather than
    # being squeezed into the axes area.
    fig.text(
        0.99, 0.01, WATERMARK_TEXT,
        ha="right", va="bottom", fontsize=8, style="italic", color="#9AA5B1", alpha=0.8,
    )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, facecolor="white")
    plt.close(fig)
    log.info(f"Chart saved -> {out_path}")
    return out_path


# ══════════════════════════════════════════════════════════════════════════
# 4. COMMENTARY (LLM or Jinja2/rule-based fallback)
# ══════════════════════════════════════════════════════════════════════════

# Language skill that governs the Summary's voice: narrative-first, three
# themed bullets (deliveries & pace / export drivers / logistics), <= 30 words
# each, and no figures that already appear in the tables below. Kept outside
# the project so it can be edited alongside the other house-style skills.
LANGUAGE_SKILL_PATH = Path(os.environ.get(
    "WEEKLY_PULSE_LANGUAGE_SKILL",
    SCRIPT_DIR.parent / "Design" / "Language_Skills" / "Lingo_CGCWeeklyReport.md",
))
SUMMARY_BULLET_COUNT: int = 3
SUMMARY_MAX_WORDS: int = 30

# Volume floor (KMT) for naming a commodity as an export outperformer /
# laggard or a stock mover. Ranking uses absolute KMT shifts in weeks 1 to
# EARLY_SEASON_MAX_WEEK and YoY % afterwards (both years >= the floor), so a
# thin base never gets named on the strength of a headline-sized % swing.
SUMMARY_MIN_BASE_KT: float = 100.0

# Bands that turn the metrics into the skill's qualitative vocabulary.
DELIVERY_WOW_BAND_PCT: float = 10.0     # |WoW| beyond this = surged / cooled off
PACE_WELL_BAND_PCT: float = 5.0         # |vs 5-yr| beyond this = "well" ahead / behind
EXPORT_PACE_BAND_PCT: float = 10.0      # total YTD exports vs LY: strong / soft
CORRIDOR_IMBALANCE_PTS: float = 15.0    # West - East utilization gap = imbalance
TIGHTENING_UTIL_PCT: float = 60.0       # West Coast storage "tightening" from here

# Week-over-week (week N vs N-1) momentum bands for the Summary.
EXPORT_WOW_BAND_PCT: float = 15.0       # total terminal shipments: accelerating / slowing
EXPORT_SURGE_PCT: float = 25.0          # one commodity's weekly exports: surge / slowdown
EXPORT_SURGE_MIN_KT: float = 50.0       # ...only when either week moved at least this much
UTIL_SHIFT_PTS: float = 2.0             # storage utilization change: tightening / easing

# Prose names: headline (title case) and in-sentence (lower case) forms.
NARRATIVE_NAMES: dict[str, tuple[str, str]] = {
    "Wheat (excl. Durum)": ("Wheat", "non-durum wheat"),
    "Amber Durum": ("Durum", "durum"),
}


# Plural crop names take plural verbs and drop the 's' before a noun
# ("lentils continue", "lentil stocks").
PLURAL_NAMES: set[str] = {"Lentils", "Peas", "Soybeans"}


def _names(m: dict) -> tuple[str, str]:
    name = m["display_name"]
    return NARRATIVE_NAMES.get(name, (name, name.lower()))


def _verb(m: dict, singular: str, plural: str) -> str:
    return plural if m["display_name"] in PLURAL_NAMES else singular


def _attributive(m: dict) -> str:
    prose = _names(m)[1]
    return prose[:-1] if m["display_name"] in PLURAL_NAMES else prose


_CITE_MARKER = re.compile(r"\[cite:[^\]]*\]")


def load_language_skill(path: Path = LANGUAGE_SKILL_PATH) -> Optional[str]:
    """The skill markdown with its '[cite: N]' source markers stripped, or
    None (logged) if the file is missing -- the rule-based writer below
    follows the same framework, so the report still builds without it."""
    try:
        return _CITE_MARKER.sub("", Path(path).read_text(encoding="utf-8")).strip()
    except OSError as exc:
        log.warning(f"Language skill not found at {path} ({exc}); using built-in summary rules.")
        return None


# Any number left after removing '5-year' is treated as table duplication.
_FIGURE = re.compile(r"\d")


def _check_bullets(bullets: list) -> list[str]:
    """Enforce the skill's hard rules on a candidate summary."""
    if not isinstance(bullets, list) or len(bullets) != SUMMARY_BULLET_COUNT:
        raise ValueError(f"expected {SUMMARY_BULLET_COUNT} summary bullets, got {bullets!r}")
    out = []
    for b in bullets:
        if not isinstance(b, str) or ": " not in b:
            raise ValueError(f"bullet needs a 'Headline: narrative' form: {b!r}")
        b = b.strip()
        if len(b.split()) > SUMMARY_MAX_WORDS:
            raise ValueError(f"bullet over {SUMMARY_MAX_WORDS} words: {b!r}")
        if _FIGURE.search(b.replace("5-year", "")):
            raise ValueError(f"bullet repeats a figure: {b!r}")
        out.append(b)
    return out


def _fit(*clauses: str) -> str:
    """Join the first clause with as many optional clauses as fit the word cap."""
    text = clauses[0]
    for c in clauses[1:]:
        if c and len((text + c).split()) <= SUMMARY_MAX_WORDS:
            text += c
    return text


def _wow_pct(cur: Optional[float], prev: Optional[float]) -> Optional[float]:
    """Week-over-week % change, None when either week is missing or zero."""
    if cur is None or not prev:
        return None
    return (cur - prev) / prev * 100.0


def _direction(change: Optional[float], band: float) -> Optional[str]:
    """'up' / 'down' / 'flat' against a +/- band, None without a comparison."""
    if change is None:
        return None
    return "up" if change >= band else "down" if change <= -band else "flat"


def _ranked_movers(commodities: list[dict], early: bool, pct_key: str,
                   cur_key: str, ly_key: str) -> list[dict]:
    """Commodities eligible to be named, sorted ascending by the comparison
    behind the wording: absolute KMT shift (cur - ly) early in the crop year,
    YoY % from week EARLY_SEASON_MAX_WEEK + 1 onward."""
    if early:
        rows = [m for m in commodities
                if m.get(cur_key) is not None and m.get(ly_key) is not None
                and max(m[cur_key], m[ly_key]) >= SUMMARY_MIN_BASE_KT]
        return sorted(rows, key=lambda m: m[cur_key] - m[ly_key])
    rows = [m for m in commodities
            if m.get(pct_key) is not None
            and min(m.get(cur_key) or 0.0, m.get(ly_key) or 0.0) >= SUMMARY_MIN_BASE_KT]
    return sorted(rows, key=lambda m: m[pct_key])


class CommentaryWriter:
    def __init__(self, use_llm: bool = USE_LLM_DEFAULT, api_key: str = ANTHROPIC_API_KEY):
        self.use_llm = use_llm and bool(api_key)
        self.api_key = api_key
        self.skill = load_language_skill()

    def write(self, payload: dict) -> dict:
        """Returns {"summary": [str, str, str]} -- 'Headline: narrative' bullets."""
        if self.use_llm and self.skill:
            try:
                return self._write_llm(payload)
            except Exception as exc:
                log.warning(f"LLM commentary failed ({exc}); falling back to rule-based text.")
        return self._write_rule_based(payload)

    # -- rule-based (deterministic, no external calls) -------------------
    # Mirrors the skill's 3-bullet framework and word bank. Bullet 1 uses
    # system-wide delivery totals only, so the Corn/Soybeans
    # no-delivery-commentary rule holds.
    def _write_rule_based(self, payload: dict) -> dict:
        macro, commodities = payload["macro"], payload["commodities"]
        prior = payload.get("prior") or {}
        prior_macro, prior_comms = prior.get("macro") or {}, prior.get("commodities") or []
        week = macro.get("week_number")
        early = week is not None and week <= EARLY_SEASON_MAX_WEEK
        bullets = [
            self._deliveries_bullet(macro, prior_macro),
            self._exports_bullet(macro, commodities, prior_macro, prior_comms, early),
            self._logistics_bullet(macro, commodities, prior_macro, early),
        ]
        return {"summary": _check_bullets(bullets)}

    @staticmethod
    def _deliveries_bullet(macro: dict, prior_macro: dict) -> str:
        """This week's intake direction (N vs N-1) read against last week's
        (N-1 vs N-2), so a slide that is flattening or reversing is called
        out rather than described in isolation."""
        wow, prev_wow = macro.get("deliveries_wow_pct"), prior_macro.get("deliveries_wow_pct")
        now = _direction(wow, DELIVERY_WOW_BAND_PCT)
        last = _direction(prev_wow, DELIVERY_WOW_BAND_PCT)
        # Two moves the same way: is the momentum building or fading?
        if now == last and now in ("up", "down"):
            last = "same_building" if abs(wow) >= abs(prev_wow) else "same_fading"
        phrases = {
            ("up", "down"): ("Farm Deliveries Rebounding", "Elevator intake rebounded after last week's pullback"),
            ("up", "same_building"): ("Farm Deliveries Accelerating", "Elevator intake extended last week's gains, signalling robust producer selling"),
            ("up", "same_fading"): ("Farm Deliveries Rising", "Elevator intake rose again, though gains are moderating from last week"),
            ("up", None): ("Farm Deliveries Accelerating", "Elevator intake surged this week, signalling robust producer selling"),
            ("down", "same_building"): ("Farm Deliveries Slowing", "Elevator intake declined for a second straight week, pointing to a producer selling pause"),
            ("down", "same_fading"): ("Farm Deliveries Easing", "Elevator intake slipped again, though the decline moderated from last week"),
            ("down", "up"): ("Farm Deliveries Cooling", "Elevator intake pulled back after last week's surge"),
            ("down", None): ("Farm Deliveries Slowing", "Elevator intake cooled off this week, pointing to a producer selling pause"),
            ("flat", "down"): ("Farm Deliveries Stabilizing", "Elevator intake levelled out after last week's decline"),
            ("flat", "up"): ("Farm Deliveries Levelling Off", "Elevator intake held last week's higher pace"),
            ("flat", None): ("Farm Deliveries Steady", "Elevator intake held a steady pace this week"),
        }
        head, intake = phrases.get((now, last)) or phrases.get((now, None)) or phrases[("flat", None)]

        vs5 = macro.get("deliveries_vs_5yr_pct")
        if vs5 is None:
            return f"{head}: {intake}."
        if vs5 >= PACE_WELL_BAND_PCT:
            pace = "running well ahead of"
        elif vs5 >= 0:
            pace = "running slightly ahead of"
        elif vs5 > -PACE_WELL_BAND_PCT:
            pace = "tracking slightly behind"
        else:
            pace = "lagging well behind"
        return _fit(f"{head}: {intake}", f", while crop-year pace is {pace} the 5-year average") + "."

    @staticmethod
    def _exports_bullet(macro: dict, commodities: list[dict], prior_macro: dict,
                        prior_comms: list[dict], early: bool) -> str:
        """Total terminal shipments N vs N-1, the commodity whose weekly
        exports accelerated (or slowed) most, then the YTD tone vs last year."""
        total = _direction(_wow_pct(macro.get("terminal_outflow_total_kt"),
                                    prior_macro.get("terminal_outflow_total_kt")), EXPORT_WOW_BAND_PCT)

        prev_by_name = {m["display_name"]: m.get("weekly_exports_kt") for m in prior_comms}
        shifts = []
        for m in commodities:
            cur, prev = m.get("weekly_exports_kt"), prev_by_name.get(m["display_name"])
            if cur is None or prev is None or max(cur, prev) < EXPORT_SURGE_MIN_KT:
                continue
            pct = _wow_pct(cur, prev)
            if pct is not None and abs(pct) >= EXPORT_SURGE_PCT:
                shifts.append((cur - prev, m))
        surge = max(shifts, key=lambda x: x[0])[1] if shifts and max(shifts, key=lambda x: x[0])[0] > 0 else None
        slump = min(shifts, key=lambda x: x[0])[1] if shifts and min(shifts, key=lambda x: x[0])[0] < 0 else None

        if surge is not None:
            name = _names(surge)[0]
            head = f"{name[:-1] if surge['display_name'] in PLURAL_NAMES else name} Exports Accelerating"
        else:
            head = {"up": "Export Flow Accelerating", "down": "Export Flow Slowing"}.get(total, "Export Flow Steady")
        base = {"up": "Terminal shipments picked up week on week",
                "down": "Terminal shipments slowed week on week",
                "flat": "Terminal shipments held steady week on week"}.get(
                    total, "Terminal export activity continued")

        if surge is None:
            surge_clause = ""
        elif total == "up":
            surge_clause = f", led by a surge in {_attributive(surge)} loadings"
        else:
            surge_clause = f", with {_attributive(surge)} loadings surging"
            base = base.replace(" week on week", " overall")
        slump_clause = f", while {_attributive(slump)} movement eased" if slump is not None else ""

        cur_ytd = sum(m.get("ytd_exports_kt") or 0.0 for m in commodities)
        ly_ytd = sum(m.get("ly_ytd_exports_kt") or 0.0 for m in commodities)
        ytd = _direction(_wow_pct(cur_ytd, ly_ytd), EXPORT_PACE_BAND_PCT)
        ytd_clause = {"up": "; YTD pace stays strong versus last year",
                      "down": "; YTD pace remains soft versus last year",
                      "flat": "; YTD pace is tracking last year"}.get(ytd, "")
        return _fit(f"{head}: {base}", surge_clause, slump_clause, ytd_clause) + "."

    @staticmethod
    def _logistics_bullet(macro: dict, commodities: list[dict], prior_macro: dict, early: bool) -> str:
        """Terminal space shift N vs N-1 by corridor (relieving vs tightening),
        anchored on where West Coast utilization now sits."""
        west, east = macro.get("west_coast_util_pct"), macro.get("east_interior_util_pct")
        if west is None or east is None:
            return "Pipeline Visibility Limited: Terminal capacity data is unavailable this week, so corridor fluidity cannot be assessed."

        def shift(now, prev):
            return _direction(None if prev is None else now - prev, UTIL_SHIFT_PTS)

        w_move = shift(west, prior_macro.get("west_coast_util_pct"))
        e_move = shift(east, prior_macro.get("east_interior_util_pct"))
        high = west >= RULE_THRESHOLDS["high_utilization_pct"]

        if w_move == "up":
            head = "West Coast Congestion Building" if high else "West Coast Space Tightening"
            state = "West Coast terminal space tightened week on week"
        elif w_move == "down":
            head = "West Coast Space Relieving"
            state = "West Coast terminal space eased week on week"
        elif high:
            head, state = "West Coast Congestion Risk", "West Coast terminals remain close to capacity"
        elif west >= TIGHTENING_UTIL_PCT:
            head, state = "West Coast Space Tight", "West Coast terminal space held tight week on week"
        else:
            head, state = "Pipeline Fluid", "West Coast terminals retain ample storage space"
        if w_move in ("up", "down"):
            state += (", with storage still near capacity" if high
                      else ", leaving ample room" if west < TIGHTENING_UTIL_PCT else "")

        also = " also" if e_move == w_move and e_move in ("up", "down") else ""
        east_clause = {"up": f"; eastern corridors{also} tightened",
                       "down": f"; eastern corridors{also} gained space",
                       "flat": "; eastern corridors held steady"}.get(e_move, "")

        stock_clause = ""
        ranked = _ranked_movers(commodities, early, "stocks_yoy_pct", "stocks_kt", "stocks_ly_kt")
        if ranked:
            key = ((lambda m: abs(m["stocks_kt"] - m["stocks_ly_kt"])) if early
                   else (lambda m: abs(m["stocks_yoy_pct"])))
            mover = max(ranked, key=key)
            building = mover["stocks_kt"] > mover["stocks_ly_kt"] if early else mover["stocks_yoy_pct"] > 0
            stock_clause = (f"; {_attributive(mover)} stocks are "
                            f"{'building above' if building else 'drawing below'} last year")
        return _fit(f"{head}: {state}", east_clause, stock_clause) + "."

    # -- optional LLM path (Anthropic Messages API) -----------------------
    def _write_llm(self, payload: dict) -> dict:
        import requests
        week = payload["macro"].get("week_number")
        prompt = (
            "Write the SUMMARY section of this week's Canadian Grain Handling Weekly, "
            "following the language skill exactly.\n"
            f"Hard constraints: exactly {SUMMARY_BULLET_COUNT} bullets in the framework order "
            "(Farm Deliveries & Pace; Export Demand & Commodity Drivers; Logistics & Pipeline "
            "Health). Each bullet is 'Short Headline: narrative sentence', at most "
            f"{SUMMARY_MAX_WORDS} words in total. Use NO numbers at all -- no tonnages, "
            "percentages or counts -- because the tables below already show them; '5-year "
            "average' is the only allowed figure.\n"
            "Corn and Soybeans: CGC primary-elevator delivery data isn't comprehensive in Eastern "
            "Canada, so never characterize their deliveries; talk only about their exports and "
            "stocks.\n"
            f"Crop-year week {week}: in weeks 1-{EARLY_SEASON_MAX_WEEK} judge commodity "
            "outperformers/laggards by absolute volume change vs last year, not % change. Never "
            f"name a commodity as a mover when either year's volume is under {SUMMARY_MIN_BASE_KT:.0f} KMT.\n"
            "WEEK-OVER-WEEK: payload.prior holds the same metrics for the previous grain week. "
            "Contrast this week against it rather than describing this week in isolation: is a "
            "delivery decline flattening, reversing or extending; are terminal exports accelerating "
            "or slowing, and which commodity's weekly exports surged or eased most; is West Coast "
            "and eastern terminal space relieving or tightening. Keep it directional -- no numbers.\n"
            "Base every judgement on the payload; do not invent facts.\n"
            "Respond ONLY with JSON: {\"summary\": [str, str, str]}\n\n"
            f"PAYLOAD:\n{json.dumps(payload, default=str)}"
        )
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": ANTHROPIC_MODEL,
                "max_tokens": 500,
                "system": self.skill,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        text = "".join(
            block.get("text", "") for block in resp.json().get("content", [])
            if block.get("type") == "text"
        )
        text = text.strip().strip("`").removeprefix("json").strip()
        return {"summary": _check_bullets(json.loads(text).get("summary"))}


# ══════════════════════════════════════════════════════════════════════════
# 5. HTML RENDERING (Jinja2)
# ══════════════════════════════════════════════════════════════════════════

def fmt_pct(v: Optional[float]) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    return f"{v:+.1f}%"


def fmt_pct_number(v: Optional[float]) -> str:
    """Same as fmt_pct but WITHOUT the trailing '%' -- used in the
    Commodity Flow & YTD table, where cells must be strictly numerical and
    the '% vs LY' unit lives in the header/ribbon row instead."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    return f"{v:+.1f}"


def fmt_kt(v: Optional[float]) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    return f"{v:,.0f}"


def fmt_mmt(v: Optional[float]) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    return f"{v:,.2f}"


# Full-document wrapper for the local HTML file only. Ghost gets the bare
# fragment (render_report_html): it drops <style> blocks in newsletters, and
# the page needs its own scroll behaviour. The height/overflow clamp is
# print-only, so previews scroll; print/PDF is a strict 1-page A4 portrait.
DOCUMENT_SHELL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
/* Base web view: scrollable (no height/overflow clamp outside print) */
html, body {{
  height: auto;
  overflow: visible;
  font-size: 9.5pt;
  line-height: 1.25;
}}

body {{
  margin: 0;
  padding: 8px;
  background: #FFFFFF;
}}

/* Tight whitespace & spacing controls */
section, .report-section {{
  margin-bottom: 10px;
}}

h2, h3, .section-title {{
  margin-top: 0;
  margin-bottom: 4px;
}}

table {{
  width: 100%;
  border-collapse: collapse;
  font-size: 9pt;
}}

td, th {{
  padding: 4px 6px;
}}

/* Strict single-page print boundary */
@media print {{
  @page {{
    size: A4 portrait;
    margin: 8mm 10mm;
  }}
  html, body {{
    max-height: 100vh;
    overflow: hidden;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
  }}
  body {{
    padding: 0;
  }}
  .no-break {{
    page-break-inside: avoid;
  }}
}}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def render_document_html(fragment: str, title: str) -> str:
    from html import escape
    return DOCUMENT_SHELL.format(title=escape(title), body=fragment)


def fmt_util(v: Optional[float]) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "N/A"
    return f"{v:.0f}%"


def render_report_html(payload: dict, week_label: str, crop_year_label: str) -> str:
    from jinja2 import Environment, FileSystemLoader

    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))
    env.filters["pct"] = fmt_pct
    env.filters["pctnum"] = fmt_pct_number
    env.filters["kt"] = fmt_kt
    env.filters["mmt"] = fmt_mmt
    env.filters["util"] = fmt_util
    template = env.get_template(TEMPLATE_NAME)

    week_number = payload["macro"].get("week_number")
    is_early_season = week_number is not None and week_number <= EARLY_SEASON_MAX_WEEK

    return template.render(
        macro=payload["macro"],
        commodities=payload["commodities"],
        summary=payload["summary"],
        is_early_season=is_early_season,
        week_label=week_label,
        crop_year_label=crop_year_label,
        footer_url=FOOTER_URL,
        publication_title=PUBLICATION_TITLE,
        generated_at=payload["macro"].get("generated_at", ""),
    )


# ══════════════════════════════════════════════════════════════════════════
# 6. GHOST ADMIN API
# ══════════════════════════════════════════════════════════════════════════

class GhostPublisher:
    def __init__(self, api_url: str = GHOST_API_URL, admin_api_key: str = GHOST_ADMIN_API_KEY):
        self.api_url = api_url
        self.admin_api_key = admin_api_key

    def _token(self) -> str:
        import jwt
        key_id, secret = self.admin_api_key.split(":")
        iat = int(datetime.now().timestamp())
        header = {"alg": "HS256", "typ": "JWT", "kid": key_id}
        payload = {"iat": iat, "exp": iat + 5 * 60, "aud": "/admin/"}
        return jwt.encode(payload, bytes.fromhex(secret), algorithm="HS256", headers=header)

    def _headers(self) -> dict:
        return {"Authorization": f"Ghost {self._token()}"}

    def upload_image(self, image_path: Path) -> str:
        import requests
        url = f"{self.api_url}/ghost/api/admin/images/upload/"
        with open(image_path, "rb") as f:
            files = {"file": (Path(image_path).name, f, "image/png")}
            resp = requests.post(url, headers=self._headers(), files=files, timeout=60)
        resp.raise_for_status()
        return resp.json()["images"][0]["url"]

    def create_draft_post(self, title: str, html: str, tags: list[str],
                           feature_image_url: Optional[str] = None) -> dict:
        import requests
        url = f"{self.api_url}/ghost/api/admin/posts/?source=html"
        post = {
            "title": title,
            "html": html,
            "status": "draft",
            "tags": [{"name": t} for t in tags],
        }
        if feature_image_url:
            post["feature_image"] = feature_image_url
        resp = requests.post(url, headers=self._headers(), json={"posts": [post]}, timeout=60)
        if not resp.ok:
            log.error(f"Ghost API error {resp.status_code}: {resp.text}")
        resp.raise_for_status()
        return resp.json()

    def publish(self, title: str, html: str, chart_path: Path, tags: list[str]) -> dict:
        if not self.api_url or not self.admin_api_key:
            raise RuntimeError(
                "GHOST_API_URL / GHOST_ADMIN_API_KEY not configured. "
                "Set them as environment variables to enable publishing."
            )
        # The chart is not part of the report body; it is uploaded only so it
        # can be set as the post's feature_image (a hosted URL).
        log.info("Uploading chart image to Ghost (for feature_image) ...")
        image_url = self.upload_image(chart_path)
        log.info("Creating draft post in Ghost ...")
        result = self.create_draft_post(title, html, tags, feature_image_url=image_url)
        post_url = result.get("posts", [{}])[0].get("url", "(no url returned)")
        log.info(f"Draft created: {post_url}")
        return result


# ══════════════════════════════════════════════════════════════════════════
# ORCHESTRATION
# ══════════════════════════════════════════════════════════════════════════

def build_analytics(force_refresh: bool = False) -> CGCAnalytics:
    """Self-locating paths, CGCAnalytics as the single entry point for all data."""
    log.info(f"Looking for capacity workbook at: {CAPACITY_PATH}")
    if not CAPACITY_PATH.exists():
        raise SystemExit(
            f"CGC_Capacity workbook was not found at {CAPACITY_PATH}. "
            f"Copy/move your CGC_Capacity.xlsb, .xlsx, or .xls file into "
            f"{DATA_DIR} (or set CGC_CAPACITY_FILE)."
        )
    analytics = CGCAnalytics(
        gsw_data_dir=str(GSW_DATA_DIR),
        capacity_xlsb_path=str(CAPACITY_PATH),
        years=DEFAULT_CROP_YEARS,
        current_year=DEFAULT_CURRENT_YEAR,
    )
    log.info("Loading GSW + capacity data via CGCAnalytics ...")
    analytics.refresh(force_refresh=force_refresh)
    return analytics


def run(week: Optional[int] = None, use_llm: bool = USE_LLM_DEFAULT,
        publish: bool = True, force_refresh: bool = False,
        analytics: Optional[CGCAnalytics] = None) -> Path:
    """Build the report. Pass an already-refreshed `analytics` (as
    run_report.py does) to skip a second GSW download."""
    if analytics is None:
        analytics = build_analytics(force_refresh=force_refresh)
    engine = WeeklyPulseEngine(analytics)

    macro = engine.macro_snapshot(week=week)
    commodities = engine.compute_all(week=week)

    rules = RulesEngine()
    payload = rules.build_payload(macro, commodities)

    # Week N-1 snapshot so the Summary can contrast momentum week on week.
    prior_week = engine._prior_week(macro["week_number"])
    if prior_week is not None:
        payload["prior"] = {
            "week_number": prior_week,
            "macro": engine.macro_snapshot(week=prior_week),
            "commodities": [m.to_dict() for m in engine.compute_all(week=prior_week)],
        }

    commentary = CommentaryWriter(use_llm=use_llm).write(payload)
    payload.update(commentary)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    JSON_PAYLOAD_PATH.write_text(json.dumps(payload, indent=2, default=str))
    log.info(f"Objective payload written -> {JSON_PAYLOAD_PATH}")

    series_df = engine.five_year_export_series(week=macro["week_number"])
    chart_path = build_export_pacing_chart(series_df)  # Ghost feature_image only; not in the report body

    week_num = macro["week_number"]
    crop_year_label = macro["crop_year"].replace("20", "") if macro["crop_year"] else ""
    week_label = f"Week {week_num:02d}" if week_num else "Week —"

    html = render_report_html(payload, week_label, crop_year_label)
    title = f"{PUBLICATION_TITLE} - Week {week_num:02d}" if week_num else PUBLICATION_TITLE
    HTML_OUTPUT_PATH.write_text(render_document_html(html, title), encoding="utf-8")
    log.info(f"HTML report written -> {HTML_OUTPUT_PATH}")

    if publish:
        try:
            GhostPublisher().publish(
                title=title, html=html, chart_path=chart_path,
                tags=[PUBLICATION_TITLE, "Logistics", "CGC Data"],
            )
        except Exception as exc:
            log.error(f"Ghost publish skipped/failed: {exc}")
    else:
        log.info("--no-publish set: skipping Ghost draft creation.")
    return HTML_OUTPUT_PATH


def main():
    parser = argparse.ArgumentParser(description=f"Generate the {PUBLICATION_TITLE} report.")
    parser.add_argument("--week", type=int, default=None, help="Force a specific grain_week (for testing).")
    parser.add_argument("--no-llm", action="store_true", help="Force rule-based commentary, skip LLM call.")
    parser.add_argument("--no-publish", action="store_true", help="Build HTML/chart locally; skip Ghost.")
    parser.add_argument("--force-refresh", action="store_true", help="Force a full GSW/capacity re-download.")
    args = parser.parse_args()

    run(
        week=args.week,
        use_llm=(USE_LLM_DEFAULT and not args.no_llm),
        publish=not args.no_publish,
        force_refresh=args.force_refresh,
    )


if __name__ == "__main__":
    main()