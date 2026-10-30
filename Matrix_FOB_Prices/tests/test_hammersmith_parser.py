"""Tests for Hammersmith/hammersmith_parser.py.

    python -m pytest tests -q                                   # from the Matrix_FOB_Prices folder
    set HS_UPDATE_GOLDEN=1 && python -m pytest tests -q         # rewrite the golden CSVs (review the diff!)

Two layers:
  * grammar tests - hand-written expectations for every quote, header, date, rate and route form seen in
    2019-2026 posts;
  * golden tests  - every fixture post parsed and compared row-for-row with tests/fixtures/hammersmith/expected/.
"""

from __future__ import annotations

import csv
import os
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Hammersmith"))

import hammersmith_parser as hp  # noqa: E402
import hammersmith_scraper as hs  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "hammersmith"
EXPECTED = FIXTURES / "expected"
POSTS = sorted(p.name.removesuffix(".json.gz") for p in FIXTURES.glob("*.json.gz"))
NOT_REPORTS = {"2025-01-18_no_report", "2025-09-05_not_report", "2026-05-16_correction"}


@pytest.fixture(scope="module")
def parsed() -> dict[str, hp.ParseResult]:
    return {name: hp.parse_post(hs.load_post(FIXTURES / f"{name}.json.gz")) for name in POSTS}


# --------------------------------------------------------------------------- #
# Quote grammar
# --------------------------------------------------------------------------- #
NOV, JAN = date(2026, 11, 1), date(2027, 1, 1)
OCT = date(2026, 10, 1)


def months(rows):
    return [(hp.ym(r["month"]), r["price"], r["derivation"]) for r in rows]


@pytest.mark.parametrize("quote, expected", [
    ("USD 283<<287", [("2026-11", 283.0, "QUOTED"), ("2026-12", 285.0, "INTERPOLATED"), ("2027-01", 287.0, "QUOTED")]),
    # the arrow is a separator, not a direction: '>>' with a rising price is still near -> far
    ("USD 280>>285", [("2026-11", 280.0, "QUOTED"), ("2026-12", 282.5, "INTERPOLATED"), ("2027-01", 285.0, "QUOTED")]),
    ("USD 445>> 430", [("2026-11", 445.0, "QUOTED"), ("2026-12", 437.5, "INTERPOLATED"), ("2027-01", 430.0, "QUOTED")]),
    ("USD 430 -- 420 December", [("2026-11", 430.0, "QUOTED"), ("2026-12", 420.0, "QUOTED")]),
    ("USD 275 --295", [("2026-11", 275.0, "QUOTED"), ("2026-12", 285.0, "INTERPOLATED"), ("2027-01", 295.0, "QUOTED")]),
    # far-month suffix extends the curve past the header window
    ("USD 255<<258 Feb", [("2026-11", 255.0, "QUOTED"), ("2026-12", 256.0, "INTERPOLATED"),
                          ("2027-01", 257.0, "INTERPOLATED"), ("2027-02", 258.0, "QUOTED")]),
    ("USD 580>>600 m/t Sep>>Nov", [("2027-09", 580.0, "QUOTED"), ("2027-10", 590.0, "INTERPOLATED"),
                                   ("2027-11", 600.0, "QUOTED")]),
    # bid/offer = flat across the window (OND/JFM style)
    ("USD 195/200", [("2026-11", 197.5, "WINDOW"), ("2026-12", 197.5, "WINDOW"), ("2027-01", 197.5, "WINDOW")]),
    ("USD 233/236=7 new crop", [("2026-11", 234.5, "WINDOW"), ("2026-12", 234.5, "WINDOW"), ("2027-01", 234.5, "WINDOW")]),
    ("USD 280/285 December", [("2026-12", 282.5, "QUOTED")]),
    ("USD 610/630 m/t Sept.", [("2027-09", 620.0, "QUOTED")]),
    ("USD 184/186 July/Sep New", [("2027-07", 185.0, "WINDOW"), ("2027-08", 185.0, "WINDOW"), ("2027-09", 185.0, "WINDOW")]),
    ("USD 195/200 Dec/Jan", [("2026-12", 197.5, "WINDOW"), ("2027-01", 197.5, "WINDOW")]),
    # spot = report month, proxied to the NEAR month only (deferred months stay N/A)
    ("USD 285/288 spot", [("2026-10", 286.5, "QUOTED"), ("2026-11", 286.5, "SPOT_PROXY")]),
    ("USD 212/215 Spot", [("2026-10", 213.5, "QUOTED"), ("2026-11", 213.5, "SPOT_PROXY")]),
    # single price, flat to a named month
    ("USD 205 flat to Mar", [("2026-11", 205.0, "WINDOW"), ("2026-12", 205.0, "WINDOW"), ("2027-01", 205.0, "WINDOW"),
                             ("2027-02", 205.0, "WINDOW"), ("2027-03", 205.0, "WINDOW")]),
    ("USD 415 steady to Jan", [("2026-11", 415.0, "WINDOW"), ("2026-12", 415.0, "WINDOW"), ("2027-01", 415.0, "WINDOW")]),
])
def test_quote_expansion(quote, expected):
    pq = hp.parse_quote(quote)
    assert months(hp.expand_quote(pq, NOV, JAN, OCT)) == expected


def test_spot_no_proxy_when_window_starts_this_month():
    rows = hp.expand_quote(hp.parse_quote("USD 278/281 spot"), OCT, date(2026, 12, 1), OCT)
    assert months(rows) == [("2026-10", 279.5, "QUOTED")]


def test_interpolation_is_linear_over_three_steps():
    rows = hp.expand_quote(hp.parse_quote("USD 377 -- 399 October"), date(2022, 7, 1), date(2022, 9, 1),
                           date(2022, 7, 1))
    assert [r["price"] for r in rows] == [377.0, 384.33, 391.67, 399.0]
    assert [r["role"] for r in rows] == ["NEAR", "MID", "MID", "FAR"]


def test_bid_offer_kept_with_mid():
    rows = hp.expand_quote(hp.parse_quote("USD 195/200"), NOV, JAN, OCT)
    assert all((r["bid"], r["offer"], r["price"]) == (195.0, 200.0, 197.5) for r in rows)


@pytest.mark.parametrize("quote", ["No prices", "USD no prices", "No info", "N/A", "", "No price",
                                   "USD – no prices", "No stock until May"])
def test_no_quote(quote):
    assert hp.parse_quote(quote)["kind"] == "NO_QUOTE"


def test_spot_from_label():
    assert hp.parse_quote("USD 390/400", "Soymeal, 48% protein, Brazil, Rotterdam – spot")["spot"]


# --------------------------------------------------------------------------- #
# Window header and report date
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text, near, far", [
    ("ALL PRICES ARE FOR November 2026<<January 2027 Wheat, USA", "2026-11", "2027-01"),
    ("ALL PRICES ARE FOR October 2022/December 2022 Wheat", "2022-10", "2022-12"),
    ("ALL PRICES ARE FOR May/July 2019 Wheat", "2019-05", "2019-07"),
    ("ALL PRICES ARE FOR December 2019/January 2020 Wheat", "2019-12", "2020-01"),
    ("ALL PRICES ARE FOR December/February 2019 Wheat", "2018-12", "2019-02"),
    ("ALL PRICES ARE FOR January 20202/March 2020 Wheat", "2020-01", "2020-03"),     # year typo
])
def test_window(text, near, far):
    n, f = hp.parse_window(text, date(2019, 6, 1))
    assert (hp.ym(n), hp.ym(f)) == (near, far)


def test_window_followed_by_commentary():
    text = "ALL PRICES ARE FOR April 2022/June 2022 Russia and Ukraine price indications are the best estimates"
    n, f = hp.parse_window(text, date(2022, 4, 2))
    assert (hp.ym(n), hp.ym(f)) == ("2022-04", "2022-06")


@pytest.mark.parametrize("label, series", [
    ("Wheat, USA Soft Re d Winter, US Gulf", "WHT_US_SRW"),        # Word splits words inside labels
    ("Wheat , USA Soft Red Winter, US Gulf", "WHT_US_SRW"),
    ("Corn Gluten Meal, USA FOB NOLA", "CGM_US"),
    ("Corn, 2YC FOB NOLA USA", "CRN_US_NOLA"),
    ("Soymeal, 48% protein, Brazil, Rotterdam – spot", "SBM_BR_RTM"),
    ("Soymeal, 48% protein, FOB Brazil", "SBM_BR"),
])
def test_series_labels(label, series):
    assert hp.match_series(label)["id"] == series


def test_window_missing():
    assert hp.parse_window("no header here", date(2026, 1, 1)) is None


@pytest.mark.parametrize("text, expected", [
    ("Weekly Report --- October 17 2026", date(2026, 10, 17)),
    ("Weekly Report ---- October 03, 2026", date(2026, 10, 3)),
    ("Hammersmith Report --- 11 15 2025", date(2025, 11, 15)),
    ("Hammersmith Weekly Report --- Sept 06, 2025", date(2025, 9, 6)),
    ("Hammersmith Weekly Report -- Jan 07,2023", date(2023, 1, 7)),
    ("Weekly Rpt -- Sep 26, 2020 -- corrected date", date(2020, 9, 26)),
])
def test_title_dates(text, expected):
    assert hp.parse_date_text(text) == expected


def test_report_date_prefers_body_and_rejects_far_dates():
    issues: list[str] = []
    body = "WEEKLY USA GRAIN AND PROTEIN REPORT March 16, 2024 Soybeans had"
    assert hp.report_date_of(body, "", date(2024, 3, 17), issues) == date(2024, 3, 16)
    # a resend published weeks later still reports its own date only if within 10 days; else publish date
    assert hp.report_date_of("", "Weekly Report -- May 07, 2022", date(2022, 5, 8), []) == date(2022, 5, 7)
    issues = []
    assert hp.report_date_of("", "Weekly Report -- Jan 07, 2022", date(2022, 5, 8), issues) == date(2022, 5, 8)
    assert any("far from publish date" in i for i in issues)


# --------------------------------------------------------------------------- #
# Freight grammar
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("cell, low, high, country", [
    ("$46.00", 46.0, 46.0, None),
    ("$61/62.00", 61.0, 62.0, None),
    ("$ 2 9 / 3 0 .00", 29.0, 30.0, None),           # Word splits digits with spaces
    ("$ 36 /3 7 .00", 36.0, 37.0, None),
    ("$19.50/20.50", 19.5, 20.5, None),
    ("$95 Russia", 95.0, 95.0, "Russia"),
    ("Ukraine $56", 56.0, 56.0, "Ukraine"),
    ("Romania $48", 48.0, 48.0, "Romania"),
])
def test_rate(cell, low, high, country):
    assert hp.parse_rate(cell) == {"low": low, "high": high, "country": country}


@pytest.mark.parametrize("cell", ["N/A", "No info", "Steady", "X", "Closed"])
def test_rate_not_a_number(cell):
    assert hp.parse_rate(cell) is None


@pytest.mark.parametrize("cell, change", [("Steady", 0.0), ("Stady", 0.0), ("Up $4.00", 4.0),
                                          ("Down $1.00", -1.0), ("up $1.00", 1.0), ("Adjusted", None)])
def test_change(cell, change):
    assert hp.parse_change(cell) == change


@pytest.mark.parametrize("label, origin, dests, cls, size, disch, port, note", [
    ("US Gulf Algeria/Tunisia: Handi", "US Gulf", ["Algeria", "Tunisia"], "Handysize", "", "", "", ""),
    ("Brazil to Algeria /Tunisia : 25 /30 ,000 m/t", "Brazil", ["Algeria", "Tunisia"], "", "25/30,000", "", "", ""),
    ("France /Germany to Saudi Arabia: Panamax", "France/Germany", ["Saudi Arabia"], "Panamax", "", "", "", ""),
    ("US Lakes, Duluth – Europe – 30,000 MT", "US Lakes (Duluth)", ["Europe"], "", "30,000", "", "", ""),
    ("US Lakes – Morocco – 30,000 m/t", "US Lakes (Duluth)", ["Morocco"], "", "30,000", "", "", ""),
    ("Argentina to South Africa 50,000 m/t", "Argentina", ["South Africa"], "", "50,000", "", "", ""),
    ("Argentina to China: Panamax – no top off", "Argentina", ["China"], "Panamax", "", "", "", "no top off"),
    ("Argentina to Morocco: 25 /30 ,000 m/t(3,000 disch)", "Argentina", ["Morocco"], "", "25/30,000", "3,000", "", ""),
    ("France/Germany to Jordan: 35/45,000 m/t (4k disch)", "France/Germany", ["Jordan"], "", "35/45,000", "4k", "", ""),
    ("Black Sea to Saudi Arabia – Jeddah – Panamax", "Black Sea", ["Saudi Arabia"], "Panamax", "", "", "Jeddah", ""),
    ("Black Sea to East Spain -- Panamax", "Black Sea", ["Spain (East)"], "Panamax", "", "", "", ""),
    ("Black Sea to Saudi: Panamax", "Black Sea", ["Saudi Arabia"], "Panamax", "", "", "", ""),
    ("Black Sea to Kenya –- Handi", "Black Sea", ["Kenya"], "Handysize", "", "", "", ""),
    ("Russia to Turkey (Izmir)– coaster 5,000 MT", "Russia", ["Turkey"], "Coaster", "5,000", "", "Izmir", ""),
    ("Australia to China: 40-55,000 m/t – round trip", "Australia", ["China"], "", "40-55,000", "", "", "round trip"),
    ("US PNW Egypt: Panamax via Panama", "US PNW", ["Egypt"], "Panamax", "", "", "", "via panama"),
    ("Ukraine to Bangladesh, Panamax", "Ukraine", ["Bangladesh"], "Panamax", "", "", "", ""),
    ("Brazil to Turkey/Egypt: Panamax", "Brazil", ["Turkey", "Egypt"], "Panamax", "", "", "", ""),
    ("U S Gulf to Europe: Panamax (10,000 disch)", "US Gulf", ["Europe"], "Panamax", "", "10,000", "", ""),
    ("US Gu lf to Europe: Panamax", "US Gulf", ["Europe"], "Panamax", "", "", "", ""),
])
def test_route(label, origin, dests, cls, size, disch, port, note):
    r = hp.parse_route(label)
    assert (r["origin"], r["destinations"], r["vessel_class"], r["vessel_size"], r["discharge_rate"],
            r["dest_port"], r["route_note"]) == (origin, dests, cls, size, disch, port, note)


def test_multi_destination_rows_share_rate_and_keep_raw():
    rows = hp.parse_freight([["US Gulf Algeria/Tunisia: Handi", "$46.00", "Down $1.00"]],
                            {"report_date": "2026-10-17"}, [])
    assert [(r["destination"], r["freight_usd_mt"], r["destination_raw"]) for r in rows] == [
        ("Algeria", 46.0, "Algeria/Tunisia"), ("Tunisia", 46.0, "Algeria/Tunisia")]


def test_black_sea_row_splits_by_country():
    rows = hp.parse_freight([["Black Sea to China: Panamax", "$95 Russia", "Ukraine $56"]], {"report_date": "d"}, [])
    assert [(r["origin_country"], r["freight_usd_mt"]) for r in rows] == [("Russia", 95.0), ("Ukraine", 56.0)]


def test_paper_indices_dropped():
    rows = hp.parse_freight([["Baltic Dry Index 40% Capesize, 30% Panama, 30% Supermax", "2511", "Down 406"],
                             ["Freightos/Baltic Container Index", "3560", "Up 216"],
                             ["Global Container Index - Freightos", "2000", ""],
                             ["US PNW China: Panamax", "$36.00", "Steady"]], {"report_date": "d"}, [])
    assert [r["origin"] for r in rows] == ["US PNW"]


def test_outlier_flagged_but_kept():
    rows = hp.parse_freight([["US Gulf China: Panamax", "$6.00", "Up $1.00"]], {"report_date": "d"}, [])
    assert len(rows) == 1 and rows[0]["freight_usd_mt"] == 6.0 and rows[0]["quality_flag"] == "OUTLIER"


def test_closed_route_is_no_quote():
    rows = hp.parse_freight([["US Lakes – Europe – 30,000 m/t", "$00.00", "Closed"]], {"report_date": "d"}, [])
    assert (rows[0]["freight_usd_mt"], rows[0]["status_note"], rows[0]["quality_flag"]) == (None, "Closed", "NO_QUOTE")


# --------------------------------------------------------------------------- #
# Whole posts
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", POSTS)
def test_post_status(parsed, name):
    res = parsed[name]
    if name in NOT_REPORTS:
        assert res.status == "NOT_REPORT"
    else:
        assert res.status == "REPORT", res.reason
        assert not [i for i in res.issues if "UNMAPPED" in i or "UNPARSED" in i], res.issues


@pytest.mark.parametrize("name, report_date", [
    ("2024-03-17_blank_title", "2024-03-16"),        # blank title, published a day late: body date wins
    ("2026-05-30_fw", "2026-05-30"),                 # 'FW:' published 2026-06-01
    ("2022-07-02_resend", "2022-07-02"),             # 'Fw: Resending' published 2022-07-03
    ("2025-11-15", "2025-11-15"),                    # numeric title date
])
def test_report_dates(parsed, name, report_date):
    assert parsed[name].report_date.isoformat() == report_date


def test_current_report_values(parsed):
    rows = {(r["series_id"], r["delivery_ym"]): r for r in parsed["2026-10-17"].prices}
    assert parsed["2026-10-17"].window == ("2026-11", "2027-01")
    assert rows[("WHT_RU_MILL", "2026-11")]["fob_usd_mt"] == 214.0
    assert rows[("WHT_RU_MILL", "2026-12")]["fob_usd_mt"] == 216.5
    assert rows[("WHT_RU_MILL", "2026-12")]["derivation"] == "INTERPOLATED"
    assert rows[("WHT_US_HRW", "2027-01")]["fob_usd_bu"] == 8.709
    assert rows[("WHT_FR_SUP", "2026-10")]["price_type"] == "SPOT"
    assert rows[("WHT_FR_SUP", "2026-11")]["derivation"] == "SPOT_PROXY"
    assert ("WHT_FR_SUP", "2026-12") not in rows and ("WHT_FR_SUP", "2027-01") not in rows
    assert rows[("SOY_BS", "2026-11")]["quality_flag"] == "NO_QUOTE"
    freight = {r["route_id"]: r for r in parsed["2026-10-17"].freight}
    assert freight["USGULF_CHINA_PANAMAX"]["quality_flag"] == "OUTLIER"
    assert freight["BLACKSEA_RUSSIA_CHINA_PANAMAX"]["freight_usd_mt"] == 95.0
    assert not any("baltic" in r["raw_label"].lower() for r in parsed["2026-10-17"].freight)


def test_unique_keys(parsed):
    for name, res in parsed.items():
        for rows in (res.prices, res.freight):
            keys = [r["key"] for r in rows]
            assert len(keys) == len(set(keys)), name


# --------------------------------------------------------------------------- #
# Golden files (regression)
# --------------------------------------------------------------------------- #
def _as_text(rows: list[dict], columns: list[str]) -> list[dict]:
    return [{c: "" if r.get(c) is None else str(r.get(c)) for c in columns} for r in rows]


@pytest.mark.parametrize("name", [p for p in POSTS if p not in NOT_REPORTS])
@pytest.mark.parametrize("kind", ["prices", "freight"])
def test_golden(parsed, name, kind):
    columns = hp.PRICE_COLUMNS if kind == "prices" else hp.FREIGHT_COLUMNS
    actual = _as_text(getattr(parsed[name], kind), columns)
    path = EXPECTED / f"{name}_{kind}.csv"
    if os.environ.get("HS_UPDATE_GOLDEN") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=columns)
            w.writeheader()
            w.writerows(actual)
    with path.open(newline="", encoding="utf-8") as f:
        expected = list(csv.DictReader(f))
    assert actual == expected
