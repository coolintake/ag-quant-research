"""Transform: one StatCan release tab (wide) -> flat records (long).

A release tab is a copy of a StatCan table:

    [optional title row]
    header row : '' | 2024 | 2025 | 2026{1} {p} | 2024 to 2025 | 2025 to 2026
    units row  :    | thousands of acres (x3)   | % change (x2)
    data rows  : crop label | values ...
    footnotes / notes / source rows (text only)

The header row is located (first row with at least MIN_YEAR_HEADERS single-year cells), value columns
are the single-year columns whose unit is a quantity, and comparison columns (% change, 'X to Y') are
dropped. Works unchanged for production tabs (units 'thousands of tonnes').
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import date

import pandas as pd

import config

log = logging.getLogger(__name__)

RECORD_COLUMNS = ["release_code", "release_date", "release_month", "release_year", "crop_name",
                  "target_crop_year", "value", "value_column", "unit", "is_preliminary", "metric_type",
                  "source_sheet", "source_label", "source_header"]


# --------------------------------------------------------------------------- #
# Release (tab title)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Release:
    code: str           # canonical 'Mar2021'
    month: int
    month_name: str     # 'March'
    year: int
    date: date          # first day of the release month


def parse_release(sheet_name: str) -> Release | None:
    """'Mar2021' / 'March2021' / '2022July' -> Release; None when the name is not a release."""
    for pattern in config.SHEET_NAME_PATTERNS:
        m = pattern.match(str(sheet_name))
        if m:
            month = config.MONTHS.get(m.group("month").lower())
            if month is None:
                return None
            year = int(m.group("year"))
            return Release(code=f"{config.MONTH_ABBR[month]}{year}", month=month,
                           month_name=config.MONTH_NAMES[month], year=year, date=date(year, month, 1))
    return None


# --------------------------------------------------------------------------- #
# Cell cleaning
# --------------------------------------------------------------------------- #
def _text(v) -> str:
    """Cell -> plain text: NFKC (non-breaking spaces -> spaces), collapsed whitespace."""
    if v is None:
        return ""
    s = unicodedata.normalize("NFKC", str(v)).replace(" ", " ")
    return re.sub(r"\s+", " ", s).strip()


@dataclass(frozen=True)
class HeaderInfo:
    year: int | None
    is_preliminary: bool
    is_comparison: bool


def clean_year_header(value) -> HeaderInfo:
    """'2021{1}{p}' -> 2021 (preliminary); 'March 20151p' -> 2015 (preliminary); '2019 to 2020' and
    '% change' -> comparison columns (no year)."""
    s = _text(value)
    if not s:
        return HeaderInfo(None, False, False)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        y = int(value)
        return HeaderInfo(y if 1900 <= y <= 2099 else None, False, False)
    if config.COMPARISON_RE.search(s) or len(config.YEAR_RE.findall(s)) > 1:
        return HeaderInfo(None, False, True)
    m = config.YEAR_RE.search(s)
    return HeaderInfo(int(m.group(1)) if m else None, bool(config.PRELIMINARY_RE.search(s)), False)


def normalize_crop_name(label) -> str | None:
    """Standard crop name: no footnotes ('{2}', trailing '3'), single spaces, sentence case.

    'Total  wheat (including winter wheat remaining){2}' -> 'Total wheat (including winter wheat remaining)'
    'Winter wheat3' -> 'Winter wheat';  'Fall Rye' -> 'Fall rye';  '   Durum wheat' -> 'Durum wheat'
    """
    s = _text(label)
    if not s:
        return None
    s = config.FOOTNOTE_RE.sub("", s).strip()
    s = config.TRAILING_FOOTNOTE_DIGITS_RE.sub("", s).strip()
    s = re.sub(r"\s+", " ", s).strip(" .:*")
    if not s:
        return None
    s = s[0].upper() + s[1:].lower()
    return config.CROP_ALIASES.get(s, s)


def _number(v) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = _text(v).replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None          # 'x', '..', 'F', text


# --------------------------------------------------------------------------- #
# Table
# --------------------------------------------------------------------------- #
def find_header_row(rows: list[tuple]) -> int:
    for i, row in enumerate(rows[: config.MAX_HEADER_SCAN_ROWS]):
        years = [h for h in map(clean_year_header, row) if h.year is not None]
        if len(years) >= config.MIN_YEAR_HEADERS:
            return i
    raise ValueError("no header row with year columns found")


def transform_sheet(sheet_name: str, rows: list[tuple], release: Release, spec: config.TopicSpec) -> pd.DataFrame:
    """One release tab -> long records (one row per crop x target year)."""
    h = find_header_row(rows)
    header = rows[h]
    units = rows[h + 1] if h + 1 < len(rows) else ()
    value_cols: list[tuple[int, HeaderInfo, str, str, float]] = []
    for j, cell in enumerate(header):
        info = clean_year_header(cell)
        if info.year is None or info.is_comparison:
            continue
        unit = config.FOOTNOTE_RE.sub("", _text(units[j])).strip().lower() if j < len(units) else ""
        if unit not in config.UNIT_MAP:
            if unit:
                log.debug("%s: column %r unit %r is not a quantity; skipped", sheet_name, cell, unit)
            continue
        col, mult = config.UNIT_MAP[unit]
        if col != spec.value_column:
            raise ValueError(f"{sheet_name}: unit {unit!r} maps to {col}, topic {spec.name} expects {spec.value_column}")
        value_cols.append((j, info, unit, _text(cell), mult))
    if not value_cols:
        raise ValueError(f"{sheet_name}: no value columns found")
    years = [c[1].year for c in value_cols]
    if len(set(years)) != len(years):
        raise ValueError(f"{sheet_name}: duplicate target years {years}")

    records, dropped = [], 0
    for row in rows[h + 2:]:
        if not row:
            continue
        crop = normalize_crop_name(row[0])
        if crop is None:
            continue
        vals = [(_number(row[j]) if j < len(row) else None) for j, *_ in value_cols]
        if all(v is None for v in vals):
            continue                         # notes, footnotes, source lines, blank rows
        for (j, info, unit, raw_header, mult), v in zip(value_cols, vals):
            if v is None:
                dropped += 1
                continue
            records.append({
                "release_code": release.code, "release_date": release.date, "release_month": release.month_name,
                "release_year": release.year, "crop_name": crop, "target_crop_year": info.year,
                "value": v * mult, "value_column": spec.value_column, "unit": unit,
                "is_preliminary": info.is_preliminary, "metric_type": spec.metric_type,
                "source_sheet": sheet_name, "source_label": _text(row[0]), "source_header": raw_header,
            })
    df = pd.DataFrame(records, columns=RECORD_COLUMNS)
    dup = df.duplicated(["crop_name", "target_crop_year"], keep=False)
    if dup.any():
        raise ValueError(f"{sheet_name}: duplicate crop/year rows after normalization:\n"
                         f"{df.loc[dup, ['source_label', 'crop_name', 'target_crop_year']]}")
    if dropped:
        log.warning("%s: %d non-numeric value cell(s) skipped (e.g. 'x' or '..')", sheet_name, dropped)
    return df


def transform(sheets) -> pd.DataFrame:
    """All extracted sheets -> one flat frame, validated."""
    frames = []
    for s in sheets:
        df = transform_sheet(s.name, s.rows, s.release, config.TOPICS[s.topic])
        bad = df[(df["target_crop_year"] > s.release.year) | (df["target_crop_year"] < s.release.year - 5)]
        if len(bad):
            raise ValueError(f"{s.name}: target years outside release window: {sorted(bad['target_crop_year'].unique())}")
        log.info("%s -> %d records (%d crops, years %s)", s.name, len(df), df["crop_name"].nunique(),
                 sorted(df["target_crop_year"].unique()))
        frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=RECORD_COLUMNS)
