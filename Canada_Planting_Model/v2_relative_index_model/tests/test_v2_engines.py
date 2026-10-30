"""Rules of SPECIFICATION_V2.md on small synthetic inputs."""

from datetime import date

import duckdb
import numpy as np
import pandas as pd
import pytest

import config as C
from engine import forecast_engine as fe
from engine import index_engine as ie
from engine import price_engine as pe
from engine import yield_engine as ye

ZONE_PROV = {"NE SASK": "SK", "SW SASK": "SK", "S ALTA": "AB", "N ALTA": "AB", "W MAN": "MB"}


def _prices(rows):
    con = duckdb.connect()
    con.execute("""CREATE TABLE p (price_date DATE, province VARCHAR, pdq_zone VARCHAR, crop_symbol VARCHAR,
                   source VARCHAR, delivery_month VARCHAR, delivery_year INTEGER, price_cad_bu DOUBLE)""")
    for d, z, s, m, y, v in rows:
        con.execute("INSERT INTO p VALUES (?, ?, ?, ?, 'PDQ', ?, ?, ?)", [d, ZONE_PROV[z], z, s, m, y, v])
    return con


def _days(n, start=date(2024, 2, 15)):
    return [date(start.year, start.month, start.day + i) for i in range(n)]


# --- 3.1 prices -------------------------------------------------------------
def test_prompt_rolls_forward_and_needs_ten_days():
    rows = [(d, "NE SASK", "CWRS", "FEB", 2024, 7.0) for d in _days(10)]          # primary month
    rows += [(date(2024, 3, 1), "NE SASK", "CWRS", "MAR", 2024, 8.0)]             # FEB expired -> MAR
    rows += [(d, "NE SASK", "CANOLA", "FEB", 2024, 14.0) for d in _days(9)]       # only 9 days
    w = pe.window_prices(_prices(rows), years=[2024], table="p")
    r3 = w[(w["run"] == "RUN3") & (w["province"] == "SK")].set_index("crop")
    assert r3.loc["CWRS", "n_days"] == 11
    assert r3.loc["CWRS", "price"] == pytest.approx((7.0 * 10 + 8.0) / 11)
    assert np.isnan(r3.loc["CANOLA", "price"]) and r3.loc["CANOLA", "price_flags"] == "THIN_WINDOW"


def test_unit_guard_rejects_per_tonne_bids_and_durum_belt_only():
    rows = [(d, "NE SASK", "CANOLA", "FEB", 2024, 640.0) for d in _days(12)]      # $/t -> rejected
    rows += [(d, "NE SASK", "DURUM", "FEB", 2024, 30.0 - 22) for d in _days(12)]  # not belt
    rows += [(d, "SW SASK", "DURUM", "FEB", 2024, 7.5) for d in _days(12)]
    w = pe.window_prices(_prices(rows), years=[2024], table="p")
    r3 = w[(w["run"] == "RUN3") & (w["province"] == "SK")].set_index("crop")
    assert np.isnan(r3.loc["CANOLA", "price"]) and "UNIT_SUSPECT" in r3.loc["CANOLA", "price_flags"]
    assert r3.loc["DURUM", "price"] == pytest.approx(7.5)


# --- 3.2 trend yields --------------------------------------------------------
def test_trend_uses_2005_to_t_minus_1_only():
    years = list(range(2005, 2021))
    y = [30 + 0.5 * (t - 2005) for t in years]
    cansim = pd.DataFrame({"province": "SK", "crop": "CWRS", "year": years, "yield_actual": y})
    shocked = cansim.copy()
    shocked.loc[shocked["year"] == 2020, "yield_actual"] = 5.0                    # drought in T itself
    for frame in (cansim, shocked):
        t = ye.trend_yields(frame, years=[2020]).iloc[0]
        assert t["trend_n"] == 15 and t["trend_yield"] == pytest.approx(30 + 0.5 * 15)


def test_trend_needs_ten_points():
    cansim = pd.DataFrame({"province": "SK", "crop": "CWRS", "year": range(2010, 2019), "yield_actual": 40.0})
    t = ye.trend_yields(cansim, years=[2019]).iloc[0]
    assert np.isnan(t["trend_yield"]) and t["yield_flags"] == "INSUFFICIENT_YIELD_HISTORY"


# --- 4 indices ---------------------------------------------------------------
def _panel(rpa: dict) -> pd.DataFrame:
    rows = [{"province": "SK", "crop": c, "run": run, "year": y, "rpa": v, "price": 1.0}
            for (c, y), v in rpa.items() for run in C.WINDOWS]
    return pd.DataFrame(rows)


def test_base_index_relative_index_and_cwrs_signal():
    rpa = {}
    for y in range(2019, 2027):
        rpa[("CWRS", y)] = 100.0
        rpa[("CANOLA", y)] = 200.0 if y < 2026 else 300.0
    p = ie.add_indices(_panel(rpa))
    r = p[(p["run"] == "RUN3") & (p["year"] == 2026)].set_index("crop")
    assert r.loc["CANOLA", "index"] == pytest.approx(150.0)                  # base = 200
    assert r.loc["CANOLA", "rel_index"] == pytest.approx(150.0)              # CWRS index = 100
    assert r.loc["CWRS", "rel_index"] == pytest.approx(100.0)
    assert r.loc["CANOLA", "pct_d_signal"] == pytest.approx(0.5)             # 3.0 / 2.0 - 1
    assert r.loc["CWRS", "pct_d_signal"] == pytest.approx(0.5 / 0.75 - 1)    # (100/300) / (100/200) - 1
    assert r.loc["CWRS", "signal_flags"] == "CWRS_VS_CANOLA"


def test_nonpositive_rpa_nulls_ratios_and_unstable_base():
    rpa = {("CWRS", y): 100.0 for y in range(2019, 2027)}
    rpa.update({("CANOLA", y): -10.0 for y in range(2019, 2025)})            # base <= 0
    rpa.update({("CANOLA", 2025): 50.0, ("CANOLA", 2026): -5.0})
    p = ie.add_indices(_panel(rpa))
    r = p[(p["run"] == "RUN3") & (p["crop"] == "CANOLA")].set_index("year")
    assert r.loc[2025, "base_flags"] == "UNSTABLE_BASE" and np.isnan(r.loc[2025, "index"])
    assert np.isnan(r.loc[2026, "rel_rpa"])                                  # RPA <= 0


# --- 6 Plan-B -----------------------------------------------------------------
def test_plan_b_flags_nonpositive_slope():
    rng = np.random.default_rng(0)
    x = rng.normal(size=40)
    obs = pd.DataFrame({"pct_d_signal": x, "pct_d_acres": -0.3 * x + rng.normal(scale=0.01, size=40)})
    f = fe.fit_pooled(obs)
    assert f["status"] == "OK" and f["b"] < 0 and f["flag"] == "PLAN_B_NO_SIGNAL"
    assert fe.fit_pooled(obs.head(5))["status"] == "INSUFFICIENT_DATA"


def test_base_is_window_matched():
    # Durum RPA is 20% higher in Run 1 every year: with a window-matched base its Run 1 and Run 3
    # indices are identical (seasonality removed) and base_window records the window used.
    rows = []
    for y in range(2019, 2027):
        for run, mult in (("RUN1", 1.2), ("RUN2", 1.1), ("RUN3", 1.0)):
            rows += [{"province": "SK", "crop": "CWRS", "run": run, "year": y, "rpa": 100.0, "price": 1.0},
                     {"province": "SK", "crop": "DURUM", "run": run, "year": y, "rpa": (80.0 + y - 2019) * mult,
                      "price": 1.0}]
    p = ie.add_indices(pd.DataFrame(rows))
    d = p[(p["crop"] == "DURUM") & (p["year"] == 2026)].set_index("run")
    assert d.loc["RUN1", "base"] == pytest.approx(1.2 * d.loc["RUN3", "base"])
    assert d.loc["RUN1", "rel_index"] == pytest.approx(d.loc["RUN3", "rel_index"])
    assert list(d["base_window"]) == list(d.index)


def test_release_forecast_prefers_plan_a_and_gates_it():
    import export_excel as ex
    preds = pd.DataFrame({"window": "RUN3", "year": 2026, "province": ["SK", "SK", "AB"],
                          "crop": ["CANOLA", "DURUM", "CANOLA"], "b": 0.1, "pct_d_signal": [0.2, -0.2, 0.3],
                          "pct_plan_b": [0.02, -0.02, 0.03], "acres_prev": 100.0, "acres_actual": 105.0})
    obs = pd.DataFrame({"window": "RUN3", "province": ["SK", "SK", "AB"], "crop": ["CANOLA", "DURUM", "CANOLA"],
                        "pct_d_acres": [0.10, 0.08, 0.05]})
    plan_a = pd.DataFrame({"province": ["SK", "SK"], "crop": ["CANOLA", "DURUM"], "year": 2026, "window": ["R3", "Run 3"],
                           "pct_change": [-0.03, -0.12]})
    r = ex.release_forecast("RUN3", preds, obs, plan_a, 2026).set_index(["province", "crop"])
    assert r.loc[("SK", "CANOLA"), "pct_release"] == pytest.approx(-0.03)            # Plan-A wins
    assert r.loc[("SK", "CANOLA"), "gate"] == "PLAN_A_SIGN_CONFLICT"                 # opposite to Plan-B
    assert r.loc[("SK", "DURUM"), "gate"] == "PLAN_A_OUTSIDE_RANGE"                  # |-12%| > 8% history
    assert r.loc[("AB", "CANOLA"), "source"] == "Plan-B (fallback)"
    assert r.loc[("AB", "CANOLA"), "acres_release"] == pytest.approx(103.0)
