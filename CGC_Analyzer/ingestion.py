"""
ingestion.py
=============
All network and file I/O for CGC analytics: downloading/caching GSW CSVs
(`CGCDownloader`) and loading the licensed-capacity workbook
(`CapacityLoader`). No math or schema-detection logic lives here directly
-- raw GSW parsing delegates to cgc_engine.standardize_and_clean.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Union

import pandas as pd
import requests

from cgc_engine import (
    CapacityDataError,
    DEFAULT_BASE_URL,
    DEFAULT_CROP_YEARS,
    DEFAULT_CURRENT_YEAR,
    DownloadError,
    OPTIONAL_CAPACITY_COLUMNS,
    REGION_STATION_MAP,
    crop_years_between,
    standardize_and_clean,
)

logger = logging.getLogger("ingestion")
PathLike = Union[str, Path]


# ═══════════════════════════════════════════════════════════════════════════
# CGCDownloader — GSW weekly CSV download / cache / combine
# ═══════════════════════════════════════════════════════════════════════════

class CGCDownloader:
    """Downloads, caches, and combines CGC GSW weekly CSVs across crop
    years. Base URL ground truth: CGC_DB.ipynb, Cell 0.
    """

    def __init__(
        self,
        data_dir: PathLike,
        years: Iterable[str] = DEFAULT_CROP_YEARS,
        current_year: str = DEFAULT_CURRENT_YEAR,
        base_url: str = DEFAULT_BASE_URL,
        cache_dir: Optional[PathLike] = None,
        timeout: int = 60,
        max_retries: int = 3,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.years: List[str] = list(years)
        self.current_year = current_year
        self.base_url = base_url
        self.cache_dir = Path(cache_dir) if cache_dir else self.data_dir / "_cache"
        self.timeout = timeout
        self.max_retries = max_retries
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _raw_path(self, year: str) -> Path:
        return self.data_dir / f"gsw-shg-en_{year}.csv"

    def download_year(self, year: str, force: bool = False) -> Path:
        """Download one crop-year CSV with retry, unless a valid cached
        copy already exists (the current year always refreshes).

        Raises DownloadError if every retry fails and no local fallback exists.
        """
        path = self._raw_path(year)
        must_fetch = force or (year == self.current_year) or not path.exists()
        if not must_fetch:
            logger.info("Using cached raw file for %s", year)
            return path

        url = self.base_url.format(year=year)
        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                logger.info("Downloading %s -> %s (attempt %d/%d)", url, path, attempt, self.max_retries)
                resp = requests.get(url, stream=True, timeout=self.timeout)
                resp.raise_for_status()
                tmp = path.with_suffix(".tmp")
                with open(tmp, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=8192):
                        fh.write(chunk)
                tmp.replace(path)
                return path
            except requests.RequestException as exc:
                last_exc = exc
                logger.warning("Download attempt %d/%d failed: %s", attempt, self.max_retries, exc)

        if path.exists():
            logger.warning(
                "All %d download attempts failed for %s; using existing cached "
                "file at %s (may be stale).", self.max_retries, year, path,
            )
            return path

        raise DownloadError(
            f"Failed to download {year} after {self.max_retries} attempts ({last_exc}). "
            f"If this persists, the network/proxy on this machine may be blocking "
            f"grainscanada.gc.ca -- verify in a browser, or manually place the CSV "
            f"at {path} and re-run."
        ) from last_exc

    def parse_year(self, year: str, force_download: bool = False) -> pd.DataFrame:
        """Download (if needed) and fully standardize+clean one crop year."""
        path = self.download_year(year, force=force_download)
        raw = pd.read_csv(path, low_memory=False)
        return standardize_and_clean(raw, year_label=year)

    # -- crop-year rollover tracking --------------------------------------------
    @property
    def _current_year_marker_path(self) -> Path:
        return self.cache_dir / "last_current_year.txt"

    def _read_last_current_year(self) -> Optional[str]:
        path = self._current_year_marker_path
        if not path.exists():
            return None
        value = path.read_text(encoding="utf-8").strip()
        return value or None

    def _write_current_year_marker(self) -> None:
        self._current_year_marker_path.write_text(self.current_year, encoding="utf-8")

    def _catch_up_rollover(self, cached: pd.DataFrame) -> pd.DataFrame:
        """If `self.current_year` has changed since the last recorded run,
        one or more crop years that used to be "current" (and so were only
        ever incrementally refreshed, potentially catching them mid-season)
        have now rolled over to historical. Force a full re-fetch of every
        such year -- not just the most recent one, in case multiple
        rollovers happened between runs -- so a season's final weeks are
        never silently left frozen at wherever the last refresh stopped.

        No-op (returns `cached` unchanged) on the very first run (no marker
        yet) or if `self.current_year` hasn't changed.
        """
        last_current_year = self._read_last_current_year()
        if last_current_year is None or last_current_year == self.current_year:
            return cached

        outgoing_years = crop_years_between(last_current_year, self.current_year)
        if not outgoing_years:
            return cached

        logger.info(
            "Crop-year rollover detected: last known current year was %s, now %s. "
            "Re-fetching %s in full to ensure no final weeks were left stale.",
            last_current_year, self.current_year, outgoing_years,
        )
        for yr in outgoing_years:
            try:
                fresh_df = self.parse_year(yr, force_download=True)
                cached = cached[cached["crop_year"] != yr]
                cached = pd.concat([cached, fresh_df], ignore_index=True)
            except DownloadError as exc:
                logger.warning(
                    "Could not complete rollover catch-up for %s (%s); keeping "
                    "existing cached data for that year, which may still be "
                    "missing its final weeks.", yr, exc,
                )
        return cached

    def load_all(self, force_refresh: bool = False) -> pd.DataFrame:
        """Combined, standardized GSW dataset across all configured crop
        years, cached as Parquet. The current (in-progress) crop year is
        always refreshed. If a crop-year rollover has happened since the
        last run, the outgoing season(s) are automatically re-fetched in
        full first (see `_catch_up_rollover`), so switching to a new
        current year can never leave the previous one silently incomplete.
        """
        cache_path = self.cache_dir / "combined_gsw.parquet"
        if cache_path.exists() and not force_refresh:
            cached = pd.read_parquet(cache_path)
            cached = self._catch_up_rollover(cached)
            try:
                current_df = self.parse_year(self.current_year)
                if not current_df.empty:
                    cached = cached[cached["crop_year"] != current_df["crop_year"].iloc[0]]
                combined = pd.concat([cached, current_df], ignore_index=True)
            except DownloadError as exc:
                logger.warning("Could not refresh current year (%s); using cache as-is.", exc)
                combined = cached
            combined.to_parquet(cache_path, index=False)
            self._write_current_year_marker()
            return combined

        frames: List[pd.DataFrame] = []
        for yr in self.years + [self.current_year]:
            try:
                frames.append(self.parse_year(yr, force_download=force_refresh))
            except Exception as exc:  # noqa: BLE001 -- log & continue per year
                logger.error("Skipping crop year %s due to error: %s", yr, exc)
        if not frames:
            raise DownloadError("No GSW crop-year data could be loaded from any source.")
        combined = pd.concat(frames, ignore_index=True)
        combined.to_parquet(cache_path, index=False)
        self._write_current_year_marker()
        return combined

    def load_from_local_files(self, file_map: Dict[str, PathLike]) -> pd.DataFrame:
        """Offline/testing entry point: build the combined dataset from a
        {crop_year_label: csv_path} mapping instead of downloading.
        """
        frames = [standardize_and_clean(pd.read_csv(path, low_memory=False), year_label=yr) for yr, path in file_map.items()]
        combined = pd.concat(frames, ignore_index=True)
        (self.cache_dir / "combined_gsw.parquet").parent.mkdir(parents=True, exist_ok=True)
        combined.to_parquet(self.cache_dir / "combined_gsw.parquet", index=False)
        self._write_current_year_marker()
        return combined


# ═══════════════════════════════════════════════════════════════════════════
# CapacityLoader — licensed elevator capacity workbook
# ═══════════════════════════════════════════════════════════════════════════

class CapacityLoader:
    """Loads and aggregates licensed elevator capacity data from a .xlsb
    or .xlsx/.xls workbook.
    """

    EXPECTED_COLUMNS: List[str] = [
        "Province", "Station", "Railway", "Company name", "Elevator type", "Capacity (tonnes)",
    ]
    # Preferred sheet name when sheet_name isn't explicitly given. Falls
    # back to the first sheet if this name isn't found -- guards against
    # the wrong sheet being silently picked if other sheets (e.g. a
    # 'Notes' sheet documenting manual data edits) are added to the same
    # workbook and happen to sort before the data sheet.
    PREFERRED_SHEET_NAME: str = "CGC_Capacity"

    def __init__(self, path: PathLike, sheet_name: Optional[str] = None) -> None:
        self.path = Path(path)
        self.sheet_name = sheet_name
        self._df: Optional[pd.DataFrame] = None

    def load(self, force_refresh: bool = False) -> pd.DataFrame:
        """Read and clean the capacity workbook.

        Raises CapacityDataError if the file, sheet, or an expected column is missing.
        """
        if self._df is not None and not force_refresh:
            return self._df

        if not self.path.exists():
            raise CapacityDataError(f"Capacity workbook not found at: {self.path}")

        suffix = self.path.suffix.lower()
        if suffix == ".xlsb":
            df = self._read_xlsb()
        elif suffix in (".xlsx", ".xls"):
            try:
                chosen_sheet = self.sheet_name
                if chosen_sheet is None:
                    available = pd.ExcelFile(self.path, engine="openpyxl").sheet_names
                    chosen_sheet = self.PREFERRED_SHEET_NAME if self.PREFERRED_SHEET_NAME in available else 0
                df = pd.read_excel(self.path, sheet_name=chosen_sheet, engine="openpyxl")
            except ValueError as exc:
                raise CapacityDataError(f"Sheet '{self.sheet_name}' not found in {self.path}: {exc}") from exc
        else:
            raise CapacityDataError(
                f"Unsupported capacity workbook extension '{suffix}' for {self.path} "
                f"(expected .xlsb, .xlsx, or .xls)."
            )

        missing = set(self.EXPECTED_COLUMNS) - set(df.columns)
        if missing:
            raise CapacityDataError(
                f"Capacity workbook at {self.path} is missing expected column(s): "
                f"{sorted(missing)}. Found columns: {list(df.columns)}."
            )

        # Optional commodity-split columns (Commodity/Industry/Ratios), used
        # for effective per-commodity Process capacity. Kept only if
        # present, so older workbooks without them still load normally.
        present_optional = [c for c in OPTIONAL_CAPACITY_COLUMNS if c in df.columns]
        df = df[self.EXPECTED_COLUMNS + present_optional].copy()
        df["Province"] = df["Province"].astype(str).str.strip()
        df["Station"] = df["Station"].astype(str).str.strip().str.upper()
        df["Elevator type"] = df["Elevator type"].astype(str).str.strip()
        df["Capacity (tonnes)"] = pd.to_numeric(df["Capacity (tonnes)"], errors="coerce")
        df = df.dropna(subset=["Capacity (tonnes)"])
        df["Capacity (Ktonnes)"] = df["Capacity (tonnes)"] / 1000.0
        for col in present_optional:
            df[col] = df[col].apply(lambda v: "" if pd.isna(v) else str(v).strip())

        self._df = df
        return df

    def _read_xlsb(self) -> pd.DataFrame:
        try:
            from pyxlsb import open_workbook
        except ImportError as exc:
            raise CapacityDataError("pyxlsb is required to read .xlsb files: pip install pyxlsb") from exc

        with open_workbook(self.path) as wb:
            if self.sheet_name:
                sheet_name = self.sheet_name
            elif self.PREFERRED_SHEET_NAME in wb.sheets:
                sheet_name = self.PREFERRED_SHEET_NAME
            else:
                sheet_name = wb.sheets[0]
            if sheet_name not in wb.sheets:
                raise CapacityDataError(
                    f"Sheet '{sheet_name}' not found in {self.path}. Available sheets: {wb.sheets}."
                )
            with wb.get_sheet(sheet_name) as sheet:
                rows = [[c.v for c in row] for row in sheet.rows()]

        if not rows:
            raise CapacityDataError(f"Sheet '{sheet_name}' in {self.path} is empty.")
        return pd.DataFrame(rows[1:], columns=rows[0])

    def capacity_by_segment(self) -> pd.DataFrame:
        """National capacity (Ktonnes) for 'Primary Elevators', 'Process
        Elevators', and each GSW terminal region. Columns: ['segment',
        'capacity_ktonnes'].
        """
        df = self.load()
        rows = []
        base = df[df["Elevator type"].isin(["Primary", "Process"])].groupby("Elevator type")["Capacity (Ktonnes)"].sum()
        if "Primary" in base.index:
            rows.append({"segment": "Primary Elevators", "capacity_ktonnes": base["Primary"]})
        if "Process" in base.index:
            rows.append({"segment": "Process Elevators", "capacity_ktonnes": base["Process"]})

        term = df[df["Elevator type"] == "Terminal"]
        for region, stations in REGION_STATION_MAP.items():
            cap = term[term["Station"].isin(stations)]["Capacity (Ktonnes)"].sum()
            rows.append({"segment": region, "capacity_ktonnes": cap})
        return pd.DataFrame(rows)

    def capacity_by_province(self, elevator_type: str = "Primary") -> pd.DataFrame:
        """Provincial capacity breakdown. Columns: ['Province', 'capacity_ktonnes']."""
        df = self.load()
        sub = df[df["Elevator type"] == elevator_type]
        return (
            sub.groupby("Province")["Capacity (Ktonnes)"].sum().reset_index()
            .rename(columns={"Capacity (Ktonnes)": "capacity_ktonnes"})
        )

    def capacity_by_mapped_region(self, elevator_type: str, region_map: Dict[str, str]) -> pd.DataFrame:
        """Capacity grouped by an arbitrary Province -> node label mapping
        (e.g. PRIMARY_PROVINCE_MAP or PROCESS_REGION_MAP from cgc_engine).
        Rows whose Province isn't in `region_map` are excluded.

        Returns columns: ['node', 'capacity_ktonnes'].
        """
        df = self.load()
        sub = df[df["Elevator type"] == elevator_type].copy()
        sub["node"] = sub["Province"].map(region_map)
        sub = sub[sub["node"].notna()]
        return (
            sub.groupby("node")["Capacity (Ktonnes)"].sum().reset_index()
            .rename(columns={"Capacity (Ktonnes)": "capacity_ktonnes"})
        )
