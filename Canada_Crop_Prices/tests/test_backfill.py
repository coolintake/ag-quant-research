"""Tests for backfill_pdq.py and the CSV-export parsing helpers in pdq_parsing.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from backfill_pdq import BackfillError, load_exports, parse_export  # noqa: E402
from database import PriceDatabase  # noqa: E402
from pdq_parsing import (  # noqa: E402
    clean_basis,
    clean_price,
    parse_export_month,
    parse_futures_code,
    parse_payload,
)

HEADER = "MONTH,COMMODITY,ZONE,IMPORT DATE,CASH,CASH CHANGE,FUTURES MONTH,BASIS,BASIS CHANGE\n"
ROWS = (
    "NOV '25,1 CDA CANOLA,PEACE,10/01/2025,14.00,-0.10,RSX25,-1.50,0.02\n"
    "DEC '25,1 CWRS 13.5,NW SASK,10/01/2025,7.50,0.05,MWZ25,0.00,0.00\n"   # real zero basis
    "DEC '25,1 CWAD 13.0,SW SASK,10/01/2025,8.20,0.00,-,0.00,-\n"          # phantom basis
    "DEC '25,2 YELLOW PEAS,W MAN,10/01/2025,9.10,-,-,-,-\n"
    "JAN '26,1 CPSR 11.5,E MAN,10/01/2025,-,-,KEH26,-,-\n"                 # unquoted
    "MAR '26,1 CDA CANOLA,S ALTA,10/01/2025,0.00,-,RSH26,0.00,-\n"         # zero cash
)


def _write(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(HEADER + body, encoding="utf-8")
    return path


@pytest.fixture()
def db(tmp_path: Path) -> PriceDatabase:
    database = PriceDatabase(tmp_path / "bf.duckdb", tmp_path / "parquet").connect()
    yield database
    database.close()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("code", "expected"),
    [("RSX25", ("RSX25", "NOV", 2025)), ("KEH25", ("KEH25", "MAR", 2025)),
     ("ZCZ25", ("ZCZ25", "DEC", 2025)), ("MWF26", ("MWF26", "JAN", 2026)),
     (" rsx2025 ", ("RSX2025", "NOV", 2025)), ("-", (None, None, None)),
     ("", (None, None, None)), (None, (None, None, None)), ("NULL", (None, None, None)),
     ("RSA25", (None, None, None))],
)
def test_parse_futures_code(code, expected):
    assert parse_futures_code(code) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("JAN '25", ("JAN", 2025)), ("dec '15", ("DEC", 2015)), ("NOV 2026", ("NOV", 2026))],
)
def test_parse_export_month(value, expected):
    assert parse_export_month(value) == expected


@pytest.mark.parametrize("bad", ["JANUARY '25", "XYZ '25", "JAN", ""])
def test_parse_export_month_rejects(bad):
    with pytest.raises(ValueError):
        parse_export_month(bad)


def test_clean_rules():
    assert clean_price("-") is None and clean_price("0.00") is None and clean_price("") is None
    assert clean_price("9.97") == 9.97
    assert clean_basis("0.00", "1 CWAD 13.0") is None
    assert clean_basis("0.0", "2 YELLOW PEAS") is None
    assert clean_basis("-", "1 CDA CANOLA") is None
    assert clean_basis("0.00", "1 CDA CANOLA") == 0.0
    assert clean_basis("-0.71", "1 CDA CANOLA") == -0.71


def test_api_yellow_peas_zero_basis_is_null():
    snap = parse_payload({"status": "ok", "prices_imported_at": "2026-10-01 14:30", "data": [
        {"zone": "W MAN", "zone_id": 8, "month": "OCT", "commodity": "2 YELLOW PEAS",
         "basis": 0, "basis_bushels": 0, "ton_price": 330.0, "bus_price": 8.98,
         "ton_price_prev": 330.0, "bus_price_prev": 8.98}]})
    assert snap.records[0].basis_mt is None and snap.records[0].ton_price == 330.0


# --------------------------------------------------------------------------- #
# Parsing an export
# --------------------------------------------------------------------------- #
class TestParseExport:
    @pytest.fixture()
    def frame(self, tmp_path) -> pd.DataFrame:
        return parse_export(_write(tmp_path, "a.csv", ROWS)).set_index("commodity", append=True)

    def _row(self, frame: pd.DataFrame, commodity: str, month: str) -> pd.Series:
        sel = frame.xs(commodity, level="commodity")
        return sel[sel.delivery_month == month].iloc[0]

    def test_dates_and_delivery(self, frame):
        canola = self._row(frame, "1 CDA CANOLA", "NOV")
        assert canola.imported_at == pd.Timestamp("2025-10-01")
        assert (canola.delivery_month, canola.delivery_year) == ("NOV", 2025)
        assert self._row(frame, "1 CPSR 11.5", "JAN").delivery_year == 2026

    def test_futures(self, frame):
        canola = self._row(frame, "1 CDA CANOLA", "NOV")
        assert (canola.futures_code, canola.futures_month, canola.futures_year) == ("RSX25", "NOV", 2025)
        durum = self._row(frame, "1 CWAD 13.0", "DEC")
        assert durum.futures_code is None or pd.isna(durum.futures_code)
        assert pd.isna(durum.futures_year)

    def test_prices_and_units(self, frame):
        canola = self._row(frame, "1 CDA CANOLA", "NOV")
        kg = config.CROP_KG_PER_BUSHEL["CANOLA"]
        assert canola.bus_price == 14.0
        assert canola.ton_price == pytest.approx(round(14.0 * 1000 / kg, 2))
        assert canola.basis_bu == -1.5
        assert canola.basis_mt == pytest.approx(round(-1.5 * 1000 / kg, 2))
        assert canola.bus_price_prev == pytest.approx(14.10)  # cash - change

    def test_null_rules(self, frame):
        assert self._row(frame, "1 CWRS 13.5", "DEC").basis_bu == 0.0       # real zero kept
        durum = self._row(frame, "1 CWAD 13.0", "DEC")
        assert durum.bus_price == 8.2 and pd.isna(durum.basis_bu) and pd.isna(durum.basis_mt)
        peas = self._row(frame, "2 YELLOW PEAS", "DEC")
        assert pd.isna(peas.basis_bu) and pd.isna(peas.bus_price_prev)
        cpsr = self._row(frame, "1 CPSR 11.5", "JAN")
        assert pd.isna(cpsr.bus_price) and pd.isna(cpsr.ton_price)
        zero = self._row(frame, "1 CDA CANOLA", "MAR")
        assert pd.isna(zero.bus_price) and pd.isna(zero.basis_bu)           # no price -> no basis

    @pytest.mark.parametrize(
        ("body", "message"),
        [("NOV '25,1 CDA CANOLA,MARS,10/01/2025,14,-,-,-,-\n", "unknown zones"),
         ("NOV '25,OATS,PEACE,10/01/2025,14,-,-,-,-\n", "unknown commodities"),
         ("NOV '25,1 CDA CANOLA,PEACE,2025-10-01,14,-,-,-,-\n", "IMPORT DATE"),
         ("NOVEMBER,1 CDA CANOLA,PEACE,10/01/2025,14,-,-,-,-\n", "MONTH")],
    )
    def test_rejects_bad_files(self, tmp_path, body, message):
        with pytest.raises(BackfillError, match=message):
            parse_export(_write(tmp_path, "bad.csv", body))

    def test_rejects_missing_column(self, tmp_path):
        path = tmp_path / "x.csv"
        path.write_text("MONTH,COMMODITY\nNOV '25,1 CDA CANOLA\n")
        with pytest.raises(BackfillError, match="missing column"):
            parse_export(path)


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def test_backfill_is_idempotent(db, tmp_path):
    history = load_exports([_write(tmp_path, "a.csv", ROWS)])
    db.upsert_pdq_history(history)
    db.upsert_pdq_history(history)
    assert db.query_df(f"SELECT count(*) n FROM {config.RAW_TABLE}").n[0] == 6
    u = db.query_df(f"SELECT * FROM {config.UNIFIED_TABLE}")
    assert len(u) == 6
    canola = u[(u.crop_symbol == "CANOLA") & (u.delivery_month == "NOV")].iloc[0]
    assert (canola.futures_code, canola.futures_month, canola.futures_year) == ("RSX25", "NOV", 2025)
    assert canola.province == "AB" and canola.price_cad_bu == 14.0


def test_later_file_wins_on_repeated_key(tmp_path):
    first = _write(tmp_path, "a.csv", ROWS)
    second = _write(tmp_path, "b.csv", "NOV '25,1 CDA CANOLA,PEACE,10/01/2025,15.00,-,RSX25,-1.0,-\n")
    history = load_exports([first, second])
    assert len(history) == 6
    row = history[(history.commodity == "1 CDA CANOLA") & (history.delivery_month == "NOV")]
    assert row.bus_price.tolist() == [15.0]


def test_replaces_same_day_api_row(db, tmp_path):
    # A live API row at 14:30 and an export row for the same day and key.
    db.upsert_pdq_records(parse_payload({"status": "ok", "prices_imported_at": "2025-10-01 14:30",
        "data": [{"zone": "PEACE", "zone_id": 1, "month": "NOV", "commodity": "1 CDA CANOLA",
                  "basis": -60, "basis_bushels": -1.36, "ton_price": 600, "bus_price": 13.61,
                  "ton_price_prev": 600, "bus_price_prev": 13.61}]}).records)
    db.upsert_pdq_history(load_exports([_write(tmp_path, "a.csv", ROWS)]))
    raw = db.query_df(f"SELECT * FROM {config.RAW_TABLE} WHERE zone = 'PEACE'")
    assert len(raw) == 1 and raw.bus_price[0] == 14.0


def test_existing_database_is_migrated(tmp_path):
    import duckdb

    path = tmp_path / "old.duckdb"
    con = duckdb.connect(str(path))
    con.execute(f"CREATE TABLE {config.UNIFIED_TABLE} (price_date DATE, province VARCHAR, "
                "pdq_zone VARCHAR, crop_symbol VARCHAR, source VARCHAR, delivery_month VARCHAR, "
                "delivery_year INTEGER, price_cad_mt DOUBLE, price_cad_bu DOUBLE)")
    con.close()
    with PriceDatabase(path, tmp_path / "parquet") as db:
        cols = set(db.query_df(f"SELECT * FROM {config.UNIFIED_TABLE} LIMIT 0").columns)
    assert {"futures_code", "futures_month", "futures_year"} <= cols


def test_header_only_export_is_skipped(db, tmp_path):
    empty = tmp_path / "empty.csv"
    empty.write_text(HEADER, encoding="utf-8")
    assert parse_export(empty).empty
    history = load_exports([empty, _write(tmp_path, "a.csv", ROWS)])
    assert len(history) == 6
    assert db.upsert_pdq_history(history)[0] == 6
    assert load_exports([empty]).empty
