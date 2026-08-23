"""
cgc_reports.py
===============
The CGCAnalytics facade: wires ingestion.py + cgc_engine.py together and
builds presentation-ready DataFrames. Chart-building lives in
`cgc_charts.py` instead -- this file owns data assembly only, so a chart
bug and a data bug can never hide in the same place.

Dynamic "latest & greatest" defaults: `get_executive_summary`,
`get_regional_utilization_matrix`, `get_segment_capacity_snapshot`, and
`get_cumulative_pacing` all accept crop_year=None / grain_week=None and
resolve to the latest values actually present in the loaded dataset.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Union
from pathlib import Path

import numpy as np
import pandas as pd

from cgc_engine import (
    CORE_COMMODITIES,
    DEFAULT_CROP_YEARS,
    DEFAULT_CURRENT_YEAR,
    DEFAULT_OUTFLOW_DEFINITION,
    OutflowDefinition,
    PRIMARY_PROVINCE_MAP,
    PRIMARY_PROVINCE_ORDER,
    PROCESS_NATIONAL_MAP,
    PROCESS_REGION_MAP,
    PROCESS_REGION_ORDER,
    TERMINAL_LOCATIONS,
    UNREPORTED_NODES,
    bottleneck_matrix,
    capacity_utilization,
    cumulative_pacing_table,
    effective_process_capacity_by_node,
    normalize_crop_year,
    seasonal_anomaly,
    stocks_by_node,
    stocks_slice,
    stocks_to_discharge_ratio,
    system_velocity,
    to_display_grain_name,
    to_raw_grain_name,
    weekly_outflow,
)
from ingestion import CapacityLoader, CGCDownloader

PathLike = Union[str, Path]


# ═══════════════════════════════════════════════════════════════════════════
# FACADE
# ═══════════════════════════════════════════════════════════════════════════

class CGCAnalytics:
    """Facade wiring ingestion + engine together; the single class most
    callers (including app.py) should interact with.
    """

    def __init__(
        self,
        gsw_data_dir: PathLike,
        capacity_xlsb_path: PathLike,
        years: Iterable[str] = DEFAULT_CROP_YEARS,
        current_year: str = DEFAULT_CURRENT_YEAR,
        outflow_def: OutflowDefinition = DEFAULT_OUTFLOW_DEFINITION,
    ) -> None:
        self.downloader = CGCDownloader(gsw_data_dir, years=years, current_year=current_year)
        self.capacity_loader = CapacityLoader(capacity_xlsb_path)
        self.outflow_def = outflow_def

        self._gsw_df: Optional[pd.DataFrame] = None
        self._stocks: Optional[pd.DataFrame] = None
        self._outflow: Optional[pd.DataFrame] = None
        self._capacity_by_segment: Optional[pd.DataFrame] = None

    # -- lifecycle ------------------------------------------------------------
    def refresh(self, force_refresh: bool = False) -> "CGCAnalytics":
        self._gsw_df = self.downloader.load_all(force_refresh=force_refresh)
        self.capacity_loader.load(force_refresh=force_refresh)
        self._invalidate_derived()
        return self

    def load_from_local_files(self, file_map: Dict[str, PathLike]) -> "CGCAnalytics":
        self._gsw_df = self.downloader.load_from_local_files(file_map)
        self.capacity_loader.load()
        self._invalidate_derived()
        return self

    def _invalidate_derived(self) -> None:
        self._stocks = self._outflow = self._capacity_by_segment = None

    def _require_data(self) -> pd.DataFrame:
        if self._gsw_df is None:
            raise RuntimeError("Call .refresh() (or .load_from_local_files()) before use.")
        return self._gsw_df

    @property
    def stocks(self) -> pd.DataFrame:
        if self._stocks is None:
            self._stocks = stocks_slice(self._require_data())
        return self._stocks

    @property
    def outflow(self) -> pd.DataFrame:
        if self._outflow is None:
            self._outflow = weekly_outflow(self._require_data(), outflow_def=self.outflow_def)
        return self._outflow

    @property
    def capacity_by_segment(self) -> pd.DataFrame:
        if self._capacity_by_segment is None:
            self._capacity_by_segment = self.capacity_loader.capacity_by_segment()
        return self._capacity_by_segment

    @property
    def process_capacity_by_grain(self) -> pd.DataFrame:
        """National Process Elevators capacity split by commodity (via the
        Commodity/Industry/Ratios effective-capacity parsing), for use by
        `capacity_utilization` in get_executive_summary /
        get_regional_utilization_matrix.

        Every grain that appears in Process Elevators stocks gets an
        EXPLICIT row here -- 0.0 if no facility's Commodity field lists it
        -- so capacity_utilization() never silently falls back to the old
        undifferentiated national total for a commodity we have concrete
        evidence has no classified capacity (e.g. rye sitting in a
        'Process' facility whose Commodity field never mentions rye).

        Returns columns: ['segment', 'grain', 'capacity_ktonnes']. Empty
        if the workbook lacks the new columns -- capacity_utilization then
        falls back to the flat total for every commodity, exactly as
        before this fix.
        """
        raw_capacity_df = self.capacity_loader.load()
        effective = effective_process_capacity_by_node(raw_capacity_df, PROCESS_NATIONAL_MAP)
        if effective.empty:
            return pd.DataFrame(columns=["segment", "grain", "capacity_ktonnes"])

        out = effective.rename(columns={"node": "segment", "commodity": "grain",
                                         "effective_capacity_ktonnes": "capacity_ktonnes"})
        # self.stocks uses RAW GSW grain names (e.g. 'Amber Durum'), but
        # compute_effective_capacity normalizes to DISPLAY names (e.g.
        # 'Durum') internally -- convert back so the merge in
        # capacity_utilization() actually matches. Without this, a future
        # workbook that classifies e.g. Durum capacity would silently be
        # treated as unclassified (0.0) instead of matching the real value.
        out["grain"] = out["grain"].apply(to_raw_grain_name)
        all_grains = self.stocks.loc[self.stocks["segment"] == "Process Elevators", "grain"].unique()
        missing = [g for g in all_grains if g not in set(out["grain"])]
        if missing:
            out = pd.concat([
                out,
                pd.DataFrame({"segment": "Process Elevators", "grain": missing, "capacity_ktonnes": 0.0}),
            ], ignore_index=True)
        return out

    # -- "latest & greatest" resolution -----------------------------------------
    def _resolve_crop_year(self, crop_year: Optional[str]) -> str:
        if crop_year is not None:
            return normalize_crop_year(crop_year)
        years = sorted(self._require_data()["crop_year"].dropna().unique())
        if not years:
            raise ValueError("No crop years found in the loaded dataset.")
        return years[-1]

    def _resolve_grain_week(self, crop_year: str, grain_week: Optional[int]) -> int:
        if grain_week is not None:
            return int(grain_week)
        sub = self._require_data()
        sub = sub[sub["crop_year"] == crop_year]
        if sub.empty or sub["grain_week"].dropna().empty:
            raise ValueError(f"No data found for crop_year={crop_year!r}.")
        return int(sub["grain_week"].max())

    # -- executive/tabular reporting API ------------------------------------------
    def get_executive_summary(
        self, commodity: str, crop_year: Optional[str] = None, grain_week: Optional[int] = None,
    ) -> pd.DataFrame:
        crop_year = self._resolve_crop_year(crop_year)
        grain_week = self._resolve_grain_week(crop_year, grain_week)
        raw_commodity = to_raw_grain_name(commodity)

        util = capacity_utilization(self.stocks, self.capacity_by_segment, self.process_capacity_by_grain)
        ratio = stocks_to_discharge_ratio(self.stocks, self.outflow)
        vel = system_velocity(self.stocks, self.outflow)
        tags = bottleneck_matrix(util, vel)

        f = lambda df: df[(df["grain"] == raw_commodity) & (df["crop_year"] == crop_year) & (df["grain_week"] == grain_week)]
        util_f, ratio_f, tags_f = f(util), f(ratio), f(tags)

        out = util_f.merge(tags_f[["segment", "velocity", "velocity_context", "bottleneck_tag"]], on="segment", how="left")
        out = out.merge(ratio_f[["grain", "crop_year", "grain_week", "weeks_of_supply"]],
                         on=["grain", "crop_year", "grain_week"], how="left")
        out["grain"] = commodity  # restore display name (e.g. 'Durum', not 'Amber Durum')
        cols = ["grain", "crop_year", "grain_week", "segment", "stocks_ktonnes", "capacity_ktonnes",
                "capacity_utilization_pct", "weeks_of_supply", "velocity", "velocity_context", "bottleneck_tag"]
        return out[cols].sort_values("segment").reset_index(drop=True)

    def get_regional_utilization_matrix(
        self, crop_year: Optional[str] = None, grain_week: Optional[int] = None,
    ) -> pd.DataFrame:
        crop_year = self._resolve_crop_year(crop_year)
        grain_week = self._resolve_grain_week(crop_year, grain_week)

        util = capacity_utilization(self.stocks, self.capacity_by_segment, self.process_capacity_by_grain)
        vel = system_velocity(self.stocks, self.outflow)
        tags = bottleneck_matrix(util, vel)
        out = tags[(tags["crop_year"] == crop_year) & (tags["grain_week"] == grain_week)].copy()

        # True only for Process Elevators when the workbook has real
        # Commodity/Industry/Ratios data -- i.e. capacity_ktonnes genuinely
        # differs per grain for that segment (canola crush vs. barley
        # malting), unlike Primary Elevators/terminals where it's the same
        # shared pool for every commodity. Consumed by
        # build_bottleneck_matrix_fig()'s "Total" column to decide whether
        # to SUM capacity across selected grains (grain-specific, separate
        # non-overlapping pools) or take a single shared value (broadcast,
        # summing would multiply-count one physical capacity pool).
        out["capacity_is_commodity_specific"] = (
            (out["segment"] == "Process Elevators") & (not self.process_capacity_by_grain.empty)
        )
        return out.sort_values(["grain", "segment"]).reset_index(drop=True)

    def get_historical_anomaly_tracker(self, commodity: str, grain_week: int, lookback_years: int = 3) -> pd.DataFrame:
        raw_commodity = to_raw_grain_name(commodity)
        anomaly = seasonal_anomaly(self.outflow, lookback_years=lookback_years)
        out = anomaly[(anomaly["grain"] == raw_commodity) & (anomaly["grain_week"] == grain_week)].copy()
        out["grain"] = commodity  # restore display name
        return out.sort_values("crop_year").reset_index(drop=True)

    def get_seasonal_pacing_anomaly(
        self, crop_year: Optional[str] = None, grain_week: Optional[int] = None, lookback_years: int = 3,
    ) -> pd.DataFrame:
        """Current-week Z-score deviation from the `lookback_years`
        historical pace, one row per commodity, for the diverging Seasonal
        Pacing Anomaly chart. This is the SAME `seasonal_anomaly()` engine
        math `get_historical_anomaly_tracker` already uses (year-over-year,
        one commodity) -- just sliced the other way: every commodity, one
        (crop_year, grain_week) snapshot. No new engine work.
        """
        crop_year = self._resolve_crop_year(crop_year)
        grain_week = self._resolve_grain_week(crop_year, grain_week)
        anomaly = seasonal_anomaly(self.outflow, lookback_years=lookback_years)
        out = anomaly[(anomaly["crop_year"] == crop_year) & (anomaly["grain_week"] == grain_week)].copy()
        out["grain"] = out["grain"].apply(to_display_grain_name)
        return out.sort_values("z_score").reset_index(drop=True)

    # -- dashboard data assembly ---------------------------------------------------
    def get_segment_capacity_snapshot(
        self,
        segment_type: str,
        crop_year: Optional[str] = None,
        grain_week: Optional[int] = None,
        commodities: Optional[List[str]] = None,
    ) -> pd.DataFrame:
        """Wide-format snapshot for `build_stacked_capacity_fig`: one row
        per node (province / east-west bucket / terminal), one column per
        selected commodity (stocks_ktonnes), plus `capacity_ktonnes`,
        `total_stocks_ktonnes`, `pct_utilized`, and
        `capacity_is_commodity_specific`.

        `segment_type`: 'primary_province' | 'process_east_west' | 'terminal'
        `commodities`: subset of CORE_COMMODITIES to include (default: all
        6). For 'process_east_west', when the capacity workbook has the
        Commodity/Industry/Ratios columns, `capacity_ktonnes` is
        recomputed as the sum of EFFECTIVE capacity for exactly this
        commodity subset (e.g. selecting just 'Canola' shows only canola
        crush capacity, not the region's total Process capacity including
        malting/milling/ethanol facilities that could never process
        canola). Falls back to undifferentiated total capacity --
        flagged via `capacity_is_commodity_specific=False` -- if those
        columns aren't present in the workbook.
        """
        crop_year = self._resolve_crop_year(crop_year)
        grain_week = self._resolve_grain_week(crop_year, grain_week)
        commodities = list(commodities) if commodities else list(CORE_COMMODITIES)

        stocks_long = stocks_by_node(self._require_data(), segment_type)
        stocks_long = stocks_long[
            (stocks_long["crop_year"] == crop_year) & (stocks_long["grain_week"] == grain_week)
            & (stocks_long["grain"].isin(commodities))
        ]

        capacity_is_commodity_specific = False

        if segment_type == "primary_province":
            capacity = self.capacity_loader.capacity_by_mapped_region("Primary", PRIMARY_PROVINCE_MAP)
            west_total = capacity.loc[capacity["node"] != "Western Canada Total", "capacity_ktonnes"].sum()
            capacity = pd.concat(
                [capacity, pd.DataFrame([{"node": "Western Canada Total", "capacity_ktonnes": west_total}])],
                ignore_index=True,
            )
            node_order = PRIMARY_PROVINCE_ORDER
        elif segment_type == "process_east_west":
            raw_capacity_df = self.capacity_loader.load()
            effective = effective_process_capacity_by_node(raw_capacity_df, PROCESS_REGION_MAP, commodities=commodities)
            if not effective.empty:
                capacity = effective.groupby("node", as_index=False)["effective_capacity_ktonnes"].sum() \
                    .rename(columns={"effective_capacity_ktonnes": "capacity_ktonnes"})
                capacity_is_commodity_specific = True
            else:
                capacity = self.capacity_loader.capacity_by_mapped_region("Process", PROCESS_REGION_MAP)
            node_order = PROCESS_REGION_ORDER
        elif segment_type == "terminal":
            capacity = self.capacity_by_segment[self.capacity_by_segment["segment"].isin(TERMINAL_LOCATIONS)] \
                .rename(columns={"segment": "node"})
            node_order = TERMINAL_LOCATIONS
        else:
            raise ValueError(f"Unknown segment_type: {segment_type!r}")

        if stocks_long.empty:
            wide = pd.DataFrame({"node": node_order})
            for g in commodities:
                wide[g] = 0.0
        else:
            wide = stocks_long.pivot_table(index="node", columns="grain", values="stocks_ktonnes", aggfunc="sum", fill_value=0.0)
            for g in commodities:
                if g not in wide.columns:
                    wide[g] = 0.0
            wide = wide[commodities].reindex(node_order, fill_value=0.0).reset_index()
            wide = wide.rename(columns={wide.columns[0]: "node"})

        out = wide.merge(capacity, on="node", how="left")
        out["capacity_ktonnes"] = out["capacity_ktonnes"].fillna(0.0)
        out["total_stocks_ktonnes"] = out[commodities].sum(axis=1)
        out["pct_utilized"] = np.where(
            out["capacity_ktonnes"] > 0, out["total_stocks_ktonnes"] / out["capacity_ktonnes"] * 100.0, np.nan,
        )
        unreported = UNREPORTED_NODES.get(segment_type, set())
        out["is_reported"] = ~out["node"].isin(unreported)
        out["capacity_is_commodity_specific"] = capacity_is_commodity_specific
        return out

    def get_cumulative_pacing(
        self, commodity: str, crop_year: Optional[str] = None, lookback_years: int = 3,
    ) -> pd.DataFrame:
        """Data for `build_cumulative_pacing_fig`: current crop year's
        cumulative YTD outflow (MMT) vs. the lookback_years historical
        min/max/average envelope, one row per grain_week.
        """
        return cumulative_pacing_table(
            self._require_data(), commodity, crop_year=crop_year,
            outflow_def=self.outflow_def, lookback_years=lookback_years,
        )

    def get_pacing_summary_table(
        self, crop_year: Optional[str] = None, grain_week: Optional[int] = None, lookback_years: int = 3,
    ) -> pd.DataFrame:
        """One row per CORE_COMMODITIES commodity, plus a TOTAL row: YTD
        (current cumulative outflow at the latest/given grain_week),
        Last Yr. (the single PRIOR crop year's cumulative at that same
        week -- not the blended multi-year average), and 3-Yr Avg (reusing
        `cumulative_pacing_table`'s existing hist_avg, the same number the
        pacing chart's envelope is built from). All volumes in Ktonnes
        (= kMT). No new engine math: "Last Yr." is looked up directly from
        `OutflowDefinition.aggregate()`, which `cumulative_pacing_table`
        already uses internally.
        """
        crop_year = self._resolve_crop_year(crop_year)
        grain_week = self._resolve_grain_week(crop_year, grain_week)

        cum_all = self.outflow_def.aggregate(self._require_data())
        all_years = sorted(cum_all["crop_year"].unique())
        idx = all_years.index(crop_year) if crop_year in all_years else len(all_years)
        prior_year = all_years[idx - 1] if idx >= 1 else None

        rows = []
        for commodity in CORE_COMMODITIES:
            raw_commodity = to_raw_grain_name(commodity)
            pacing = cumulative_pacing_table(
                self._require_data(), raw_commodity, crop_year=crop_year,
                outflow_def=self.outflow_def, lookback_years=lookback_years,
            )
            current_row = pacing[pacing["grain_week"] == grain_week]
            ytd = current_row["current_cum_mmt"].iloc[0] * 1000.0 if not current_row.empty else float("nan")
            hist_avg = current_row["hist_avg_mmt"].iloc[0] * 1000.0 if not current_row.empty else float("nan")

            if prior_year is not None:
                prior_match = cum_all[
                    (cum_all["grain"] == raw_commodity) & (cum_all["crop_year"] == prior_year)
                    & (cum_all["grain_week"] == grain_week)
                ]
                last_yr = prior_match["cum_ktonnes"].iloc[0] if not prior_match.empty else float("nan")
            else:
                last_yr = float("nan")

            rows.append({
                "commodity": commodity, "ytd_ktonnes": ytd,
                "last_yr_ktonnes": last_yr, "avg3yr_ktonnes": hist_avg,
            })

        out = pd.DataFrame(rows)
        out["vs_last_yr_pct"] = (out["ytd_ktonnes"] - out["last_yr_ktonnes"]) / out["last_yr_ktonnes"] * 100.0
        out["vs_3yr_pct"] = (out["ytd_ktonnes"] - out["avg3yr_ktonnes"]) / out["avg3yr_ktonnes"] * 100.0

        total = {
            "commodity": "TOTAL",
            "ytd_ktonnes": out["ytd_ktonnes"].sum(skipna=True),
            "last_yr_ktonnes": out["last_yr_ktonnes"].sum(skipna=True),
            "avg3yr_ktonnes": out["avg3yr_ktonnes"].sum(skipna=True),
        }
        total["vs_last_yr_pct"] = (
            (total["ytd_ktonnes"] - total["last_yr_ktonnes"]) / total["last_yr_ktonnes"] * 100.0
            if total["last_yr_ktonnes"] else float("nan")
        )
        total["vs_3yr_pct"] = (
            (total["ytd_ktonnes"] - total["avg3yr_ktonnes"]) / total["avg3yr_ktonnes"] * 100.0
            if total["avg3yr_ktonnes"] else float("nan")
        )
        return pd.concat([out, pd.DataFrame([total])], ignore_index=True)


