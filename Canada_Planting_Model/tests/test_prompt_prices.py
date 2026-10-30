"""Prompt rule (spec 4.1) and fallback hierarchy (spec 4.3) on a small in-memory price table."""

from datetime import date

import duckdb
import pandas as pd
import pytest

import prompt_prices as pp
import settings as S

ZONE_PROV = {"S ALTA": "AB", "N ALTA": "AB", "SW SASK": "SK", "NE SASK": "SK", "W MAN": "MB"}


def _table(rows):
    """rows: (price_date, zone, crop_symbol, delivery_month, delivery_year, price_cad_bu)."""
    con = duckdb.connect()
    con.execute("""CREATE TABLE prices (price_date DATE, province VARCHAR, pdq_zone VARCHAR, crop_symbol VARCHAR,
                   source VARCHAR, delivery_month VARCHAR, delivery_year INTEGER, price_cad_bu DOUBLE)""")
    for d, zone, sym, m, y, p in rows:
        con.execute("INSERT INTO prices VALUES (?, ?, ?, ?, 'PDQ', ?, ?, ?)", [d, ZONE_PROV[zone], zone, sym, m, y, p])
    return con


def _window(rows, year=2024, run="RUN1"):
    return pp.prompt_window_prices(_table(rows), years=[year], table="prices")


def test_window_bounds_and_release_dates():
    assert pp.window_bounds(2024, "RUN1") == (date(2023, 12, 15), date(2024, 1, 15))
    assert pp.window_bounds(2024, "RUN3") == (date(2024, 2, 15), date(2024, 3, 15))
    assert pp.release_date(2027, "RUN1") == date(2027, 1, 16)
    assert pp.release_date(2024, "RUN3") == date(2024, 3, 16)


def test_primary_month_is_preferred_over_later_months():
    w = _window([(date(2023, 12, 18), "NE SASK", "CWRS", "DEC", 2023, 7.0),
                 (date(2023, 12, 18), "NE SASK", "CWRS", "JAN", 2024, 9.0)])
    row = w[(w.run == "RUN1") & (w.crop == "CWRS")].iloc[0]
    assert row.price == 7.0 and row.primary_share == 1.0 and row.periods == "DEC 2023"


def test_rolls_to_earliest_later_month_when_primary_missing_or_null():
    w = _window([(date(2024, 1, 8), "NE SASK", "CWRS", "DEC", 2023, None),    # NULL primary
                 (date(2024, 1, 8), "NE SASK", "CWRS", "FEB", 2024, 9.0),
                 (date(2024, 1, 8), "NE SASK", "CWRS", "JAN", 2024, 8.0),
                 (date(2024, 1, 9), "NE SASK", "CWRS", "MAR", 2024, 10.0)])   # primary unlisted
    row = w[(w.run == "RUN1") & (w.crop == "CWRS")].iloc[0]
    assert row.price == pytest.approx((8.0 + 10.0) / 2)
    assert row.primary_share == 0.0
    assert row.periods == "JAN 2024, MAR 2024"


def test_zero_price_is_null_and_new_crop_months_are_never_used():
    w = _window([(date(2024, 1, 8), "NE SASK", "CWRS", "JAN", 2024, 0.0),     # zero -> NULL
                 (date(2024, 1, 8), "NE SASK", "CWRS", "AUG", 2024, 8.5),     # new crop: beyond JUL cap
                 (date(2024, 1, 9), "NE SASK", "CWRS", "NOV", 2023, 6.0)])    # expired, before start month
    assert w[(w.run == "RUN1") & (w.crop == "CWRS")].empty


def test_daily_zone_mean_then_window_mean():
    # Day 1: two zones (6, 8) -> 7; day 2: one zone 10 -> window mean (7 + 10) / 2 = 8.5, not (6+8+10)/3.
    w = _window([(date(2023, 12, 18), "NE SASK", "CANOLA", "DEC", 2023, 6.0),
                 (date(2023, 12, 18), "SW SASK", "CANOLA", "DEC", 2023, 8.0),
                 (date(2023, 12, 19), "NE SASK", "CANOLA", "DEC", 2023, 10.0)])
    row = w[(w.run == "RUN1") & (w.crop == "CANOLA")].iloc[0]
    assert row.price == pytest.approx(8.5)
    assert (row.n_days, row.n_zone_days) == (2, 3)


def test_durum_uses_belt_zones_only():
    w = _window([(date(2023, 12, 18), "NE SASK", "DURUM", "DEC", 2023, 20.0),    # not in the belt
                 (date(2023, 12, 18), "SW SASK", "DURUM", "DEC", 2023, 8.0),
                 (date(2023, 12, 18), "N ALTA", "DURUM", "DEC", 2023, 20.0),     # not in the belt
                 (date(2023, 12, 18), "W MAN", "DURUM", "DEC", 2023, 20.0)])     # MB: no belt
    d = w[(w.run == "RUN1") & (w.crop == "DURUM")].set_index("province")["price"]
    assert d.to_dict() == {"SK": 8.0}


def test_window_edges_are_inclusive_and_runs_are_separate():
    w = _window([(date(2023, 12, 15), "NE SASK", "CWRS", "DEC", 2023, 6.0),
                 (date(2024, 1, 15), "NE SASK", "CWRS", "JAN", 2024, 8.0),     # in Run 1 (JAN fallback) and Run 2 (primary)
                 (date(2024, 1, 16), "NE SASK", "CWRS", "JAN", 2024, 10.0)])   # Run 2 only
    by_run = w[w.crop == "CWRS"].set_index("run")["price"]
    assert by_run["RUN1"] == pytest.approx(7.0)
    assert by_run["RUN2"] == pytest.approx(9.0)


@pytest.mark.parametrize("value, unit, crop, expected", [
    (250, "$/MT", "BARLEY", 250 / (1000 / 21.7724)),   # 45.9297 bu/MT
    (5, "$/bu", "OATS", 5.0),
    (22, "ct/lb", "LENTILS", 0.22),                      # lentils stay $/lb
    (13, "ct/lb", "Y_PEAS", 13 / 100 * 60),             # peas to $/bu at 60 lb/bu
    (500, "$/MT", "CANOLA", 500 / (1000 / 22.6796)),
    (5, "bu/ac", "OATS", None),                          # not a price unit
])
def test_unit_conversion(value, unit, crop, expected):
    got = pp.to_model_price(value, unit, crop)
    assert got == (None if expected is None else pytest.approx(expected, rel=1e-4))


def test_hierarchy_order_and_flags():
    prompt = pd.DataFrame([{"province": "SK", "year": 2027, "run": "RUN1", "crop": "CWRS", "price": 7.0,
                            "n_days": 20, "n_zone_days": 80, "primary_share": 0.5, "periods": "DEC 2026, JAN 2027"}])
    template = pd.DataFrame([
        {"date": date(2027, 1, 16), "province": "SK", "crop": "CWRS", "price": 99.0},     # loses to PDQ
        {"date": date(2027, 1, 16), "province": "SK", "crop": "BARLEY", "price": 5.44},
        {"date": date(2027, 1, 15), "province": "SK", "crop": "OATS", "price": 4.0},      # wrong date: not matched
    ])
    guide = pd.DataFrame([{"province": "SK", "year": 2027, "crop": "OATS", "guide_price": 3.9},
                          {"province": "SK", "year": 2027, "crop": "BARLEY", "guide_price": 5.0}])
    r = pp.resolve_prices(prompt, template, guide, years=[2027]).set_index(["province", "run", "crop"])
    assert r.loc[("SK", "RUN1", "CWRS"), ["price", "tier"]].tolist() == [7.0, S.TIER_PDQ]
    assert r.loc[("SK", "RUN1", "BARLEY"), ["price", "tier"]].tolist() == [5.44, S.TIER_TEMPLATE]
    assert r.loc[("SK", "RUN1", "OATS"), ["price", "tier"]].tolist() == [3.9, S.TIER_GUIDE]
    assert r.loc[("SK", "RUN2", "BARLEY"), "tier"] == S.TIER_GUIDE          # template is Run 1's release date only
    missing = r.loc[("SK", "RUN1", "FLAX")]
    assert pd.isna(missing["price"]) and missing["tier"] == S.TIER_NULL
    assert len(r) == len(S.PROVINCES) * len(S.WINDOWS) * len(S.CROPS)
