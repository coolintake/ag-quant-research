"""Pytest suite for PDQ parsing, storage, manual overrides, and window averages.

Run from the project root::

    python -m pytest tests -q
"""

from __future__ import annotations

import copy
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from database import PriceDatabase  # noqa: E402
from ingest import save_raw_snapshot  # noqa: E402
from manual_override import ManualOverrideError, import_csv, validate_frame  # noqa: E402
from pdq_client import (  # noqa: E402
    PDQAPIError,
    PDQClient,
    parse_payload,
    resolve_delivery_year,
)
import price_aggregator  # noqa: E402
from price_aggregator import (  # noqa: E402
    compute_window_averages,
    target_delivery_months,
    window_bounds,
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def _row(zone: str, month: str, commodity: str, ton: float, bus: float,
         prev_t: float | None = None, prev_b: float | None = None,
         basis: float = -10.0, basis_bu: float = -0.25) -> dict[str, Any]:
    return {
        "zone": zone, "zone_id": config.PDQ_ZONE_IDS[zone], "month": month,
        "commodity": commodity, "basis": basis, "basis_bushels": basis_bu,
        "ton_price": ton, "bus_price": bus,
        "ton_price_prev": ton if prev_t is None else prev_t,
        "bus_price_prev": bus if prev_b is None else prev_b,
    }


@pytest.fixture()
def payload() -> dict[str, Any]:
    """A trimmed PDQ response shaped like the live API (2026-09-30)."""
    return {
        "status": "ok",
        "message": "",
        "prices_imported_at": "2026-09-30 14:30",
        "data": [
            # Non-quoted cell: PDQ sends zeros.
            {"zone": "PEACE", "zone_id": 1, "month": "SEP", "commodity": "1 CDA CANOLA",
             "basis": 0, "basis_bushels": 0, "ton_price": 0, "bus_price": 0,
             "ton_price_prev": 763.98, "bus_price_prev": 17.33},
            _row("PEACE", "OCT", "1 CDA CANOLA", 766.89, 17.39, basis=-56.81, basis_bu=-1.29),
            _row("SE SASK", "JAN", "1 CWRS 13.5", 318.14, 8.66, prev_t=0, prev_b=0),
            _row("W MAN", "NOV", "2 YELLOW PEAS", 330.0, 8.98),
            _row("S ALTA", "DEC", "1 CWAD 13.0", 300.0, 8.16),
            _row("NE SASK", "OCT", "1 CPSR 11.5", 272.15, 7.41),
            # Junk rows that must be skipped, not crash the load.
            _row("PEACE", "OCT", "1 CDA CANOLA", 1.0, 1.0),               # duplicate key
            {**_row("PEACE", "OCT", "1 CWRS 13.5", 1.0, 1.0), "commodity": "OATS"},
            {**_row("PEACE", "OCT", "1 CWRS 13.5", 1.0, 1.0), "zone": "MARS"},
        ],
    }


@pytest.fixture()
def db(tmp_path: Path) -> PriceDatabase:
    database = PriceDatabase(tmp_path / "test.duckdb", tmp_path / "parquet").connect()
    yield database
    database.close()


def _snapshot(stamp: str, rows: list[dict[str, Any]]):
    return parse_payload({"status": "ok", "prices_imported_at": stamp, "data": rows})


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
class TestParsing:
    def test_valid_rows_and_skips(self, payload):
        snap = parse_payload(payload)
        assert snap.imported_at == datetime(2026, 9, 30, 14, 30)
        assert len(snap.records) == 6  # 3 junk rows skipped

    def test_zero_prices_become_null(self, payload):
        rec = parse_payload(payload).records[0]
        assert rec.ton_price is None and rec.bus_price is None
        assert rec.basis_mt is None and rec.basis_bu is None
        assert rec.ton_price_prev == 763.98

    def test_zero_prev_becomes_null(self, payload):
        rec = next(r for r in parse_payload(payload).records if r.zone == "SE SASK")
        assert rec.ton_price == 318.14
        assert rec.ton_price_prev is None and rec.bus_price_prev is None

    def test_basis_kept_when_quoted(self, payload):
        rec = parse_payload(payload).records[1]
        assert (rec.basis_mt, rec.basis_bu) == (-56.81, -1.29)

    @pytest.mark.parametrize(
        ("month", "imported", "expected"),
        [("SEP", datetime(2026, 9, 30), 2026), ("DEC", datetime(2026, 9, 30), 2026),
         ("JAN", datetime(2026, 9, 30), 2027), ("AUG", datetime(2026, 9, 30), 2027),
         ("JAN", datetime(2027, 1, 15), 2027), ("OCT", datetime(2027, 1, 15), 2027)],
    )
    def test_delivery_year(self, month, imported, expected):
        assert resolve_delivery_year(month, imported) == expected

    def test_string_and_null_values(self):
        snap = _snapshot("2026-09-30 14:30", [
            {**_row("PEACE", "OCT", "1 CWRS 13.5", 0, 0), "ton_price": "311.61",
             "bus_price": None, "basis": ""},
        ])
        rec = snap.records[0]
        assert rec.ton_price == 311.61 and rec.bus_price is None and rec.basis_mt is None

    @pytest.mark.parametrize(
        "bad",
        [{"status": "error", "message": "bad key"},
         {"status": "ok", "prices_imported_at": "2026-09-30 14:30", "data": []},
         {"status": "ok", "prices_imported_at": "yesterday", "data": [{}]},
         [1, 2, 3]],
    )
    def test_bad_payload_raises(self, bad):
        with pytest.raises(PDQAPIError):
            parse_payload(bad)


class _FakeResponse:
    def __init__(self, status: int, body: Any = None, text: str = "") -> None:
        self.status_code, self._body, self.text = status, body, text
        self.ok = 200 <= status < 300

    def json(self) -> Any:
        if self._body is None:
            raise ValueError("no json")
        return self._body


class _FakeSession:
    def __init__(self, response: _FakeResponse | Exception) -> None:
        self.response = response

    def get(self, url: str, timeout: float) -> _FakeResponse:
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def close(self) -> None:
        pass


class TestClient:
    def test_fetch_ok(self, payload):
        client = PDQClient(session=_FakeSession(_FakeResponse(200, payload)))
        assert len(client.fetch().records) == 6

    @pytest.mark.parametrize(
        "response",
        [_FakeResponse(503, text="down"), _FakeResponse(200, None, "<html>"),
         requests.ConnectionError("no route")],
    )
    def test_fetch_errors(self, response):
        with pytest.raises(PDQAPIError):
            PDQClient(session=_FakeSession(response)).fetch_json()


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #
class TestDatabase:
    def test_upsert_is_idempotent(self, db, payload):
        snap = parse_payload(payload)
        db.upsert_pdq_records(snap.records)
        db.upsert_pdq_records(snap.records)
        assert db.query_df(f"SELECT count(*) n FROM {config.RAW_TABLE}").n[0] == 6
        assert db.query_df(f"SELECT count(*) n FROM {config.UNIFIED_TABLE}").n[0] == 6

    def test_nulls_stored_as_null(self, db, payload):
        db.upsert_pdq_records(parse_payload(payload).records)
        row = db.query_df(
            f"SELECT * FROM {config.RAW_TABLE} WHERE zone='PEACE' AND delivery_month='SEP'"
        ).iloc[0]
        assert pd.isna(row.ton_price) and pd.isna(row.basis_mt)
        assert db.query_df(
            f"SELECT count(*) n FROM {config.UNIFIED_TABLE} WHERE price_cad_mt = 0"
        ).n[0] == 0

    def test_unified_mapping(self, db, payload):
        db.upsert_pdq_records(parse_payload(payload).records)
        u = db.query_df(f"SELECT * FROM {config.UNIFIED_TABLE}")
        pairs = set(zip(u.pdq_zone, u.province, u.crop_symbol))
        assert ("SE SASK", "SK", "CWRS") in pairs
        assert ("W MAN", "MB", "Y_PEAS") in pairs
        assert ("S ALTA", "AB", "DURUM") in pairs
        assert ("NE SASK", "SK", "CPSR") in pairs
        assert set(u.source) == {"PDQ"}
        jan = u[u.delivery_month == "JAN"].iloc[0]
        assert jan.delivery_year == 2027 and jan.price_date == pd.Timestamp("2026-09-30")

    def test_latest_import_of_day_wins(self, db):
        db.upsert_pdq_records(_snapshot("2026-10-01 09:00",
                                        [_row("PEACE", "OCT", "1 CWRS 13.5", 300, 8.16)]).records)
        db.upsert_pdq_records(_snapshot("2026-10-01 14:30",
                                        [_row("PEACE", "OCT", "1 CWRS 13.5", 310, 8.44)]).records)
        assert db.query_df(f"SELECT count(*) n FROM {config.RAW_TABLE}").n[0] == 2
        u = db.query_df(f"SELECT * FROM {config.UNIFIED_TABLE}")
        assert len(u) == 1 and u.price_cad_mt[0] == 310

    def test_price_change_view(self, db, payload):
        db.upsert_pdq_records(parse_payload(payload).records)
        v = db.query_df("SELECT * FROM pdq_price_changes WHERE zone = 'SE SASK'").iloc[0]
        assert pd.isna(v.ton_change)  # prev was 0 -> ignored

    def test_parquet_partitions_and_roundtrip(self, db, payload, tmp_path):
        db.upsert_pdq_records(parse_payload(payload).records)
        db.upsert_pdq_records(_snapshot("2026-10-01 14:30",
                                        [_row("PEACE", "OCT", "1 CWRS 13.5", 310, 8.44)]).records)
        files = db.export_parquet()
        rel = {p.relative_to(tmp_path / "parquet").as_posix() for p in files}
        assert rel == {
            "year=2026/month=09/pdq_raw_prices.parquet",
            "year=2026/month=10/pdq_raw_prices.parquet",
            "year=2026/month=09/unified_cash_prices.parquet",
            "year=2026/month=10/unified_cash_prices.parquet",
        }
        back = db.read_parquet(config.UNIFIED_TABLE)
        assert len(back) == 7 and set(back.month.astype(int)) == {9, 10}

        db.con.execute(f"DELETE FROM {config.UNIFIED_TABLE} WHERE month(price_date) = 10")
        db.export_parquet()
        assert not (tmp_path / "parquet/year=2026/month=10/unified_cash_prices.parquet").exists()
        assert (tmp_path / "parquet/year=2026/month=10/pdq_raw_prices.parquet").exists()


def test_raw_snapshot_dedupe(tmp_path, payload):
    stamp = datetime(2026, 9, 30, 14, 30)
    first = save_raw_snapshot(payload, stamp, tmp_path)
    assert first is not None and first.name == "2026-09-30_pdq.json"
    assert save_raw_snapshot(payload, stamp, tmp_path) is None
    later = copy.deepcopy(payload) | {"prices_imported_at": "2026-09-30 16:00"}
    second = save_raw_snapshot(later, datetime(2026, 9, 30, 16, 0), tmp_path)
    assert second is not None and second.name == "2026-09-30_1600_pdq.json"
    assert json.loads(first.read_text())["prices_imported_at"] == "2026-09-30 14:30"


# --------------------------------------------------------------------------- #
# Manual overrides
# --------------------------------------------------------------------------- #
class TestManualOverride:
    def test_unit_conversion_and_defaults(self):
        out = validate_frame(pd.DataFrame([
            {"Price_Date": "2027-01-10", "Province": "sk", "Crop_Symbol": "r_lentils",
             "Delivery_Month": "Oct", "price_cad_bu": "20.00"},
            {"Price_Date": "2027-01-10", "Province": "SK", "Crop_Symbol": "SOYBEANS",
             "Delivery_Month": "1", "price_cad_mt": "500"},
        ]))
        lent, soy = out.iloc[0], out.iloc[1]
        assert lent.source == "MANUAL" and lent.delivery_year == 2027
        assert lent.price_cad_mt == pytest.approx(20 * 1000 / 27.2155, abs=1e-3)
        assert soy.delivery_month == "JAN" and soy.delivery_year == 2027
        assert soy.price_cad_bu == pytest.approx(500 * 27.2155 / 1000, abs=1e-3)
        assert pd.isna(lent.pdq_zone)

    def test_blank_price_is_null(self):
        out = validate_frame(pd.DataFrame([{"price_date": "2027-01-10", "province": "AB",
                                            "crop_symbol": "FLAX", "delivery_month": "OCT",
                                            "price_cad_mt": "", "price_cad_bu": None}]))
        assert pd.isna(out.price_cad_mt[0]) and pd.isna(out.price_cad_bu[0])

    @pytest.mark.parametrize(
        ("field", "value"),
        [("province", "ON"), ("crop_symbol", "KALE"), ("delivery_month", "XYZ"),
         ("price_cad_mt", "0"), ("price_cad_mt", "-5"), ("price_cad_mt", "abc"),
         ("source", "PDQ"), ("pdq_zone", "PEACE"), ("price_date", "not a date")],
    )
    def test_rejects_bad_rows(self, field, value):
        row = {"price_date": "2027-01-10", "province": "SK", "crop_symbol": "FLAX",
               "delivery_month": "OCT", "price_cad_mt": "600", field: value}
        with pytest.raises(ManualOverrideError):
            validate_frame(pd.DataFrame([row]))

    def test_rejects_duplicates_and_missing_columns(self):
        row = {"price_date": "2027-01-10", "province": "SK", "crop_symbol": "FLAX",
               "delivery_month": "OCT", "price_cad_mt": "600"}
        with pytest.raises(ManualOverrideError, match="share a key"):
            validate_frame(pd.DataFrame([row, row]))
        with pytest.raises(ManualOverrideError, match="price_cad_mt"):
            validate_frame(pd.DataFrame([{k: v for k, v in row.items() if k != "price_cad_mt"}]))

    def test_import_csv_upserts(self, db, tmp_path):
        csv = tmp_path / "manual.csv"
        csv.write_text("price_date,province,crop_symbol,delivery_month,price_cad_mt,source\n"
                       "2027-01-10,SK,R_LENTILS,OCT,700,BARCHART\n")
        import_csv(csv, db)
        csv.write_text("price_date,province,crop_symbol,delivery_month,price_cad_mt,source\n"
                       "2027-01-10,SK,R_LENTILS,OCT,720,BARCHART\n")
        import_csv(csv, db)
        u = db.query_df(f"SELECT * FROM {config.UNIFIED_TABLE}")
        assert len(u) == 1 and u.price_cad_mt[0] == 720 and u.source[0] == "BARCHART"


# --------------------------------------------------------------------------- #
# Window aggregation
# --------------------------------------------------------------------------- #
def test_window_bounds():
    assert window_bounds(2027, "RUN1") == (date(2026, 12, 15), date(2027, 1, 15))
    assert window_bounds(2027, "RUN2") == (date(2027, 1, 15), date(2027, 2, 15))
    assert window_bounds(2027, "RUN3") == (date(2027, 2, 15), date(2027, 3, 15))
    with pytest.raises(ValueError):
        window_bounds(2027, "RUN9")


class TestAggregator:
    @pytest.fixture()
    def loaded(self, db):
        # RUN2 (Jan 15 - Feb 15, 2027), new-crop OCT 2027 canola, SK zones.
        db.upsert_pdq_records(_snapshot("2027-01-15 14:30", [
            _row("NW SASK", "OCT", "1 CDA CANOLA", 700, 15.88),
            _row("SW SASK", "OCT", "1 CDA CANOLA", 720, 16.33),
            _row("NE SASK", "OCT", "1 CDA CANOLA", 0, 0),        # no quote -> NULL
            _row("PEACE", "OCT", "1 CDA CANOLA", 650, 14.74),
            _row("NW SASK", "MAR", "1 CDA CANOLA", 999, 22.65),  # old crop, excluded
        ]).records)
        db.upsert_pdq_records(_snapshot("2027-02-01 14:30", [
            _row("NW SASK", "OCT", "1 CDA CANOLA", 740, 16.78),
        ]).records)
        db.upsert_pdq_records(_snapshot("2027-02-16 14:30", [   # outside RUN2
            _row("NW SASK", "OCT", "1 CDA CANOLA", 9999, 226.8),
        ]).records)
        return db

    def _sk(self, frame: pd.DataFrame, window: str = "RUN2") -> pd.Series:
        sel = frame[(frame.window == window) & (frame.province == "SK")]
        assert len(sel) == 1
        return sel.iloc[0]

    def test_simple(self, loaded):
        out = compute_window_averages(loaded, 2027, windows=["RUN2"])
        sk = self._sk(out)
        assert sk.avg_price_cad_mt == pytest.approx((700 + 720 + 740) / 3)
        assert sk.n_obs == 3 and sk.n_days == 2
        ab = out[out.province == "AB"].iloc[0]
        assert ab.avg_price_cad_mt == 650

    def test_weighted_equal_days(self, loaded):
        out = compute_window_averages(loaded, 2027, windows=["RUN2"], method="weighted")
        # Day 1 zone mean = 710, day 2 = 740 -> each day counts once.
        assert self._sk(out).avg_price_cad_mt == pytest.approx((710 + 740) / 2)

    def test_weighted_zone_weights(self, loaded):
        out = compute_window_averages(loaded, 2027, windows=["RUN2"], method="weighted",
                                      zone_weights={"NW SASK": 3.0, "SW SASK": 1.0})
        day1 = (3 * 700 + 1 * 720) / 4
        assert self._sk(out).avg_price_cad_mt == pytest.approx((day1 + 740) / 2)

    def test_boundaries_inclusive(self, loaded):
        out = compute_window_averages(loaded, 2027, windows=["RUN1", "RUN3"])
        # Jan 15 sits in RUN1; Feb 16 sits in RUN3; Feb 1 in neither.
        assert self._sk(out, "RUN1").avg_price_cad_mt == pytest.approx(710)
        assert self._sk(out, "RUN3").avg_price_cad_mt == pytest.approx(9999)

    def test_filters_and_empty(self, loaded):
        assert compute_window_averages(loaded, 2027, crop_symbols=["CWRS"]).empty
        old = compute_window_averages(loaded, 2027, windows=["RUN2"], delivery_months=["MAR"])
        assert self._sk(old).avg_price_cad_mt == 999
        with pytest.raises(ValueError):
            compute_window_averages(loaded, 2027, method="median")  # type: ignore[arg-type]

    def test_fixture_runs_on_fallback(self, loaded):
        # The fixture only quotes OCT canola, so the NOV target falls back.
        out = compute_window_averages(loaded, 2027, windows=["RUN2"])
        assert set(out.month_basis) == {"fallback"}
        assert set(out.delivery_months) == {"AUG,SEP,OCT,NOV,DEC"}

    def test_sources_not_blended(self, loaded, tmp_path):
        csv = tmp_path / "m.csv"
        csv.write_text("price_date,province,crop_symbol,delivery_month,price_cad_mt\n"
                       "2027-01-20,SK,CANOLA,OCT,100\n")
        import_csv(csv, loaded)
        out = compute_window_averages(loaded, 2027, windows=["RUN2"])
        sk = out[(out.province == "SK")].set_index("source")
        assert sk.loc["MANUAL", "avg_price_cad_mt"] == 100
        assert sk.loc["PDQ", "avg_price_cad_mt"] == pytest.approx(720)


# --------------------------------------------------------------------------- #
# Per-crop target delivery months
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("crop", "expected"),
    [("CWRS", ("DEC",)), ("CPSR", ("DEC",)), ("DURUM", ("DEC",)), ("CORN", ("DEC",)),
     ("Y_PEAS", ("DEC",)), ("CANOLA", ("NOV",)), ("SOYBEANS", ("NOV",)),
     (" canola ", ("NOV",)), ("R_LENTILS", config.NEW_CROP_DELIVERY_MONTHS),
     ("OATS", ("AUG", "SEP", "OCT", "NOV", "DEC"))],
)
def test_target_delivery_months(crop, expected):
    assert target_delivery_months(crop) == expected


class TestTargetMonths:
    @pytest.fixture()
    def loaded(self, db, tmp_path):
        # RUN2 2027: canola quoted for OCT and NOV, CWRS for OCT and DEC,
        # durum only for OCT (target DEC unquoted -> fallback).
        db.upsert_pdq_records(_snapshot("2027-01-20 14:30", [
            _row("NW SASK", "OCT", "1 CDA CANOLA", 700, 15.88),
            _row("NW SASK", "NOV", "1 CDA CANOLA", 710, 16.10),
            _row("NW SASK", "OCT", "1 CWRS 13.5", 300, 8.16),
            _row("NW SASK", "DEC", "1 CWRS 13.5", 310, 8.44),
            _row("NW SASK", "OCT", "1 CWAD 13.0", 290, 7.89),
            _row("NW SASK", "DEC", "1 CDA CANOLA", 0, 0),   # NULL, ignored
        ]).records)
        csv = tmp_path / "lentils.csv"
        csv.write_text("price_date,province,crop_symbol,delivery_month,price_cad_mt\n"
                       "2027-01-20,SK,R_LENTILS,SEP,600\n"
                       "2027-01-20,SK,R_LENTILS,OCT,620\n")
        import_csv(csv, db)
        return db

    def _row_for(self, out: pd.DataFrame, crop: str) -> pd.Series:
        sel = out[(out.crop_symbol == crop) & (out.province == "SK")]
        assert len(sel) == 1
        return sel.iloc[0]

    def test_target_month_used(self, loaded):
        out = compute_window_averages(loaded, 2027, windows=["RUN2"])
        canola, cwrs = self._row_for(out, "CANOLA"), self._row_for(out, "CWRS")
        assert canola.avg_price_cad_mt == 710 and canola.delivery_months == "NOV"
        assert cwrs.avg_price_cad_mt == 310 and cwrs.delivery_months == "DEC"
        assert canola.month_basis == cwrs.month_basis == "target"

    def test_fallback_when_target_unquoted(self, loaded, caplog):
        with caplog.at_level("WARNING", logger="price_aggregator"):
            out = compute_window_averages(loaded, 2027, windows=["RUN2"])
        durum = self._row_for(out, "DURUM")
        assert durum.avg_price_cad_mt == 290
        assert durum.month_basis == "fallback"
        assert durum.delivery_months == "AUG,SEP,OCT,NOV,DEC"
        assert any("DURUM" in r.message and "falling back" in r.message for r in caplog.records)
        # Crops whose target was quoted do not warn.
        assert not any("CANOLA" in r.message for r in caplog.records)

    def test_untargeted_crop_uses_default_range(self, loaded):
        out = compute_window_averages(loaded, 2027, windows=["RUN2"])
        lentils = self._row_for(out, "R_LENTILS")
        assert lentils.avg_price_cad_mt == pytest.approx(610)
        assert lentils.month_basis == "default"

    def test_explicit_months_override(self, loaded, caplog):
        with caplog.at_level("WARNING", logger="price_aggregator"):
            out = compute_window_averages(loaded, 2027, windows=["RUN2"], delivery_months=["oct"])
        assert not caplog.records  # explicit months never fall back
        assert set(out.month_basis) == {"explicit"} and set(out.delivery_months) == {"OCT"}
        assert self._row_for(out, "CANOLA").avg_price_cad_mt == 700
        assert self._row_for(out, "CWRS").avg_price_cad_mt == 300
        assert compute_window_averages(loaded, 2027, windows=["RUN2"],
                                       delivery_months=["DEC"], crop_symbols=["DURUM"]).empty

    def test_crop_filter_with_targets(self, loaded):
        out = compute_window_averages(loaded, 2027, windows=["RUN2"], crop_symbols=["canola"])
        assert list(out.crop_symbol.unique()) == ["CANOLA"]
        assert out.delivery_months.iloc[0] == "NOV"

    def test_cli_months_default_none(self):
        assert price_aggregator._parse_args(["--crop-year", "2027"]).months is None
        assert price_aggregator._parse_args(
            ["--crop-year", "2027", "--months", "OCT", "NOV"]).months == ["OCT", "NOV"]
