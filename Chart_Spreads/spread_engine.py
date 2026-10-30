"""Build calendar spreads from parsed futures data and align them seasonally.

Spread = Leg1 Close - Leg2 Close, joined on Date (inner join).
CropYear = Leg 1 contract year. X-axis alignment is anchored on the first day of
Leg 1's delivery month, so every year lines up regardless of exchange-specific
last-trading-day rules or truncated download history.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from data_loader import MONTH_CODES, MONTH_NAMES, canonical_root

# Spread definitions: label -> (leg1 month, leg2 month, year offset applied to leg 2).
GRAIN_SPREADS = {
    "Z-H": ("Z", "H", 1),  # Dec / Mar next year
    "H-K": ("H", "K", 0),  # Mar / May
    "K-N": ("K", "N", 0),  # May / Jul
    "N-Z": ("N", "Z", 0),  # Jul / Dec
}
OILSEED_SPREADS = {
    "H-K": ("H", "K", 0),  # Mar / May
    "K-N": ("K", "N", 0),  # May / Jul
    "N-X": ("N", "X", 0),  # Jul / Nov
    "X-H": ("X", "H", 1),  # Nov / Mar next year
}
# Soy oil and meal list no November contract; they use the Dec-anchored cycle.
SOY_PRODUCT_SPREADS = {
    "H-K": ("H", "K", 0),
    "K-N": ("K", "N", 0),
    "N-Z": ("N", "Z", 0),
    "Z-H": ("Z", "H", 1),
}

SPREADS_BY_ROOT = {
    "MW": GRAIN_SPREADS, "KW": GRAIN_SPREADS, "W": GRAIN_SPREADS, "C": GRAIN_SPREADS,
    "RS": OILSEED_SPREADS, "ZS": OILSEED_SPREADS,
    "ZL": SOY_PRODUCT_SPREADS, "ZM": SOY_PRODUCT_SPREADS,
}

# Reference year for the "SeasonalDate" axis (a leap year, so Feb 29 has a home).
REF_YEAR = 2000


def spreads_for_root(root: str) -> dict:
    """Spread definitions applicable to a root (grain cycle for unknown roots)."""
    return SPREADS_BY_ROOT.get(canonical_root(root), GRAIN_SPREADS)


def spread_label(root: str, month1: str, month2: str) -> str:
    """Human label, e.g. 'MWZ-MWH (Dec/Mar)'."""
    r = canonical_root(root)
    return f"{r}{month1}-{r}{month2} ({MONTH_NAMES[month1]}/{MONTH_NAMES[month2]})"


def _leg(df: pd.DataFrame, root: str, month: str) -> pd.DataFrame:
    sub = df.loc[(df["Root"] == root) & (df["Month"] == month),
                 ["Date", "Symbol", "ContractYear", "Close"]]
    return sub


def build_spread(df: pd.DataFrame, root: str, month1: str, month2: str,
                 year_offset: int = 0, start_year: int = 2010, end_year: int = 2026,
                 lookback_days: int | None = 365) -> pd.DataFrame:
    """Build a calendar spread for every crop year in [start_year, end_year].

    Parameters
    ----------
    df : output of data_loader.load_futures
    root : commodity root (any alias: 'C' or 'ZC', 'KW' or 'KE', ...)
    month1, month2 : futures month codes for Leg 1 and Leg 2 (e.g. 'Z', 'H')
    year_offset : Leg 2 contract year = Leg 1 year + year_offset (1 for Z-H, X-H)
    start_year, end_year : Leg 1 contract years to include (CropYear)
    lookback_days : keep only dates within this many days before Leg 1's delivery
        month (None keeps every overlapping date)

    Returns
    -------
    DataFrame with Date, CropYear, Leg1Symbol, Leg2Symbol, Leg1Close, Leg2Close,
    Spread, DaysToExpiry, DayOfYear, SeasonalDate, Label.
    DaysToExpiry counts calendar days to the 1st of Leg 1's delivery month
    (positive before, 0 at, negative inside the delivery month).
    """
    root = canonical_root(root)
    month1, month2 = month1.upper(), month2.upper()
    if month1 not in MONTH_CODES or month2 not in MONTH_CODES:
        raise ValueError(f"Bad month code: {month1!r}, {month2!r}")

    leg1 = _leg(df, root, month1)
    leg1 = leg1[leg1["ContractYear"].between(start_year, end_year)]
    leg2 = _leg(df, root, month2).copy()
    # Shift leg 2's year back so it keys onto its leg-1 crop year.
    leg2["ContractYear"] = leg2["ContractYear"] - year_offset

    merged = leg1.merge(leg2, on=["ContractYear", "Date"], how="inner", suffixes=("1", "2"))
    columns = ["Date", "CropYear", "Leg1Symbol", "Leg2Symbol", "Leg1Close", "Leg2Close",
               "Spread", "DaysToExpiry", "DayOfYear", "SeasonalDate", "Label"]
    if merged.empty:
        return pd.DataFrame(columns=columns)

    out = pd.DataFrame({
        "Date": merged["Date"],
        "CropYear": merged["ContractYear"].astype(int),
        "Leg1Symbol": merged["Symbol1"],
        "Leg2Symbol": merged["Symbol2"],
        "Leg1Close": merged["Close1"],
        "Leg2Close": merged["Close2"],
    })
    out["Spread"] = out["Leg1Close"] - out["Leg2Close"]

    m1 = MONTH_CODES[month1]
    anchor = pd.to_datetime({"year": out["CropYear"], "month": m1, "day": 1})
    out["DaysToExpiry"] = (anchor - out["Date"]).dt.days
    out["DayOfYear"] = out["Date"].dt.dayofyear
    # Same distance from the anchor, replotted in a fixed reference year so every
    # crop year shares one date axis (month ticks read naturally).
    ref_anchor = pd.Timestamp(date(REF_YEAR, m1, 1))
    out["SeasonalDate"] = ref_anchor - pd.to_timedelta(out["DaysToExpiry"], unit="D")
    out["Label"] = spread_label(root, month1, month2)

    if lookback_days is not None:
        out = out[out["DaysToExpiry"] <= lookback_days]
    return out.sort_values(["CropYear", "Date"]).reset_index(drop=True)[columns]


def build_named_spread(df: pd.DataFrame, root: str, pair: str, **kwargs) -> pd.DataFrame:
    """Build a spread by its label, e.g. build_named_spread(df, 'MW', 'Z-H')."""
    defs = spreads_for_root(root)
    if pair not in defs:
        raise KeyError(f"{pair!r} not defined for {root}; choose from {list(defs)}")
    m1, m2, offset = defs[pair]
    return build_spread(df, root, m1, m2, offset, **kwargs)


def available_spreads(df: pd.DataFrame, root: str) -> list[str]:
    """Spread labels for a root whose both legs exist in the data."""
    root = canonical_root(root)
    months = set(df.loc[df["Root"] == root, "Month"])
    return [k for k, (m1, m2, _) in spreads_for_root(root).items() if m1 in months and m2 in months]


def seasonal_stats(spread_df: pd.DataFrame, exclude_years: list[int] | None = None,
                   last_n: int | None = 5) -> pd.DataFrame:
    """Average / min / max spread by DaysToExpiry across historical crop years.

    By default uses the last `last_n` completed years before the latest crop year.
    """
    if spread_df.empty:
        return pd.DataFrame(columns=["DaysToExpiry", "SeasonalDate", "Mean", "Min", "Max", "Years"])
    years = sorted(spread_df["CropYear"].unique())
    hist = [y for y in years[:-1] if not exclude_years or y not in exclude_years]
    if last_n:
        hist = hist[-last_n:]
    if not hist:
        return pd.DataFrame(columns=["DaysToExpiry", "SeasonalDate", "Mean", "Min", "Max", "Years"])
    # Forward-fill each year onto every calendar day of its own range, so weekends
    # and holidays falling on different days in different years don't thin the mean.
    filled = []
    for y in hist:
        s = spread_df.loc[spread_df["CropYear"] == y].set_index("DaysToExpiry")["Spread"]
        s = s[~s.index.duplicated()].sort_index()
        full = range(int(s.index.min()), int(s.index.max()) + 1)
        filled.append(s.reindex(full).ffill().rename(y))
    wide = pd.concat(filled, axis=1)
    count = wide.notna().sum(axis=1)
    wide = wide[count >= max(1, len(hist) // 2 + 1)]
    m1_anchor = spread_df["SeasonalDate"] + pd.to_timedelta(spread_df["DaysToExpiry"], unit="D")
    ref_anchor = m1_anchor.iloc[0]
    stats = pd.DataFrame({
        "DaysToExpiry": wide.index,
        "SeasonalDate": ref_anchor - pd.to_timedelta(wide.index, unit="D"),
        "Mean": wide.mean(axis=1).values,
        "Min": wide.min(axis=1).values,
        "Max": wide.max(axis=1).values,
    }).sort_values("DaysToExpiry", ascending=False)
    stats["Years"] = f"{hist[0]}-{hist[-1]}"
    return stats.reset_index(drop=True)
