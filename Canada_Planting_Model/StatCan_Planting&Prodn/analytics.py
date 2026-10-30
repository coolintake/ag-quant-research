"""Query helpers over ``plantings_estimates`` (read-only).

Every StatCan planting release reports three crop years: the current year (preliminary) and the two
previous years (revised). So for one crop and one crop year T the table holds a *revision path*:
March T (first intentions), June T (revised intentions), then March/June T+1 and T+2 (revised
seeded area).

    march_vs_june("Canola")        March vs June estimate of each year, plus the latest revision
    revision_path("Canola", 2021)  every estimate of 2021 Canola area, oldest release first
    yoy_by_release("Canola")       year-on-year change of each release's headline (preliminary) year
    release_snapshot("Jun2026")    all crops in one release
"""

from __future__ import annotations

import pandas as pd

import config
from loader import connect

TABLE = config.TOPICS["planting"].table


def _q(sql: str, params: list | None = None) -> pd.DataFrame:
    con = connect(read_only=True)
    try:
        return con.execute(sql, params or []).df()
    finally:
        con.close()


def crops() -> list[str]:
    return _q(f"SELECT DISTINCT crop_name FROM {TABLE} ORDER BY 1")["crop_name"].tolist()


def releases() -> pd.DataFrame:
    return _q(f"SELECT DISTINCT release_code, release_date FROM {TABLE} ORDER BY release_date")


def march_vs_june(crop: str) -> pd.DataFrame:
    """For each crop year T: March T estimate, June T estimate, the June-March revision and the
    latest estimate published for T (from the most recent release that reports T)."""
    return _q(f"""
        WITH e AS (SELECT * FROM {TABLE} WHERE crop_name = ?),
        mar AS (SELECT target_crop_year AS yr, acres_thousand AS march FROM e
                WHERE release_month = 'March' AND release_year = target_crop_year),
        jun AS (SELECT target_crop_year AS yr, acres_thousand AS june FROM e
                WHERE release_month = 'June' AND release_year = target_crop_year),
        latest AS (SELECT target_crop_year AS yr, arg_max(acres_thousand, release_date) AS latest,
                          arg_max(release_code, release_date) AS latest_release
                   FROM e GROUP BY 1)
        SELECT coalesce(m.yr, j.yr) AS crop_year, m.march, j.june,
               j.june - m.march AS june_minus_march,
               (j.june / m.march - 1) AS june_vs_march_pct,
               l.latest, l.latest_release,
               (l.latest / j.june - 1) AS latest_vs_june_pct
        FROM mar m FULL OUTER JOIN jun j ON m.yr = j.yr
        LEFT JOIN latest l ON l.yr = coalesce(m.yr, j.yr)
        ORDER BY crop_year""", [crop])


def revision_path(crop: str, crop_year: int) -> pd.DataFrame:
    return _q(f"""
        SELECT release_code, release_date, acres_thousand, is_preliminary,
               acres_thousand - first_value(acres_thousand) OVER (ORDER BY release_date) AS change_vs_first
        FROM {TABLE} WHERE crop_name = ? AND target_crop_year = ?
        ORDER BY release_date""", [crop, crop_year])


def yoy_by_release(crop: str, release_month: str | None = None) -> pd.DataFrame:
    """Each release's preliminary estimate vs the previous year's area as reported in the same release."""
    where = "AND cur.release_month = ?" if release_month else ""
    params = [crop] + ([release_month] if release_month else [])
    return _q(f"""
        SELECT cur.release_code, cur.release_date, cur.target_crop_year, cur.acres_thousand AS current,
               prev.acres_thousand AS previous_year, cur.acres_thousand / prev.acres_thousand - 1 AS yoy_pct
        FROM {TABLE} cur
        JOIN {TABLE} prev ON prev.release_code = cur.release_code AND prev.crop_name = cur.crop_name
                         AND prev.target_crop_year = cur.target_crop_year - 1
        WHERE cur.crop_name = ? AND cur.target_crop_year = cur.release_year {where}
        ORDER BY cur.release_date""", params)


def release_snapshot(release_code: str | None = None) -> pd.DataFrame:
    """All crops x years in one release (default: the latest)."""
    if release_code is None:
        release_code = _q(f"SELECT arg_max(release_code, release_date) AS c FROM {TABLE}")["c"].iloc[0]
    long = _q(f"SELECT crop_name, target_crop_year, acres_thousand FROM {TABLE} WHERE release_code = ?",
              [release_code])
    wide = long.pivot(index="crop_name", columns="target_crop_year", values="acres_thousand")
    wide.columns.name = release_code
    return wide.reset_index()
