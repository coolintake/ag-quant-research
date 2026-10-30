"""Seasonal price charts from ``crop_prices.duckdb``, plus pytest checks for them.

Run as a script to chart the real database into ``output/``::

    python tests/test_visualizations.py

Chart 1  output/canola_basis_october.png
    Daily average canola basis for NOV delivery (ICE November ``X`` context),
    Oct 1-15, one line per year. Basis is read from ``pdq_raw_prices`` because
    ``unified_cash_prices`` holds flat prices only.
Chart 2  output/durum_flat_price_dec_jan.png
    Daily average durum flat cash price for DEC vs JAN delivery, Dec 15-Jan 15,
    one line per season and delivery period.

"Daily average" is the mean across the PDQ zones that quoted that day, using
each day's latest PDQ import. Unquoted (NULL) prices are excluded. Lines are
keyed by delivery month *and* delivery year: a DEC bid on Dec 20 is this
December, while a DEC bid on Jan 5 is next December, so the two are kept
apart rather than averaged together.

Under pytest, the same chart code runs against a temporary synthetic
database; the real database and ``output/`` are not touched.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless: write files, never open a window

import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from database import PriceDatabase  # noqa: E402

OUTPUT_DIR: Path = config.PROJECT_ROOT / "output"
CANOLA_FILE = "canola_basis_october.png"
DURUM_FILE = "durum_flat_price_dec_jan.png"
CANOLA_TITLE = "Historical Canola Basis over Nov (X) Delivery (Oct 1–15)"
DURUM_TITLE = "Historical Durum Cash Bids: Dec vs Jan Delivery (Dec 15–Jan 15)"


# --------------------------------------------------------------------------- #
# Queries
# --------------------------------------------------------------------------- #
def load_canola_basis(db: PriceDatabase) -> pd.DataFrame:
    """Daily mean canola NOV basis across zones, Oct 1-15 of every year."""
    return db.query_df(
        f"""
        WITH latest AS (
            SELECT CAST(r.imported_at AS DATE) AS price_date, r.zone,
                   r.delivery_year, r.basis_mt, r.basis_bu
            FROM {config.RAW_TABLE} r
            JOIN pdq_commodity_map c USING (commodity)
            WHERE c.crop_symbol = 'CANOLA'
              AND r.delivery_month = 'NOV'
              AND month(r.imported_at) = 10 AND day(r.imported_at) BETWEEN 1 AND 15
            QUALIFY row_number() OVER (
                PARTITION BY CAST(r.imported_at AS DATE), r.zone_id ORDER BY r.imported_at DESC
            ) = 1
        )
        SELECT price_date, delivery_year,
               avg(basis_mt) AS basis_mt, avg(basis_bu) AS basis_bu,
               count(basis_mt) AS n_zones
        FROM latest
        GROUP BY price_date, delivery_year
        HAVING count(basis_mt) > 0
        ORDER BY price_date
        """
    )


def load_durum_flat(db: PriceDatabase) -> pd.DataFrame:
    """Daily mean durum DEC/JAN flat price across zones, Dec 15-Jan 15 of every season."""
    return db.query_df(
        f"""
        SELECT price_date, delivery_month, delivery_year,
               avg(price_cad_mt) AS price_cad_mt, avg(price_cad_bu) AS price_cad_bu,
               count(price_cad_mt) AS n_zones
        FROM {config.UNIFIED_TABLE}
        WHERE crop_symbol = 'DURUM' AND source = 'PDQ'
          AND delivery_month IN ('DEC', 'JAN')
          AND ((month(price_date) = 12 AND day(price_date) >= 15)
               OR (month(price_date) = 1 AND day(price_date) <= 15))
        GROUP BY price_date, delivery_month, delivery_year
        HAVING count(price_cad_mt) > 0
        ORDER BY price_date, delivery_year, delivery_month
        """
    )


def _coverage(db: PriceDatabase) -> str:
    row = db.con.execute(
        f"SELECT min(price_date), max(price_date) FROM {config.UNIFIED_TABLE}"
    ).fetchone()
    if not row or row[0] is None:
        return "The database is empty."
    return f"Database covers {row[0]} to {row[1]}."


# --------------------------------------------------------------------------- #
# Plotting
# --------------------------------------------------------------------------- #
def _color(i: int, n: int) -> tuple:
    """Distinct colour for series i of n (tab10 up to 10 series, tab20 beyond)."""
    return plt.get_cmap("tab10" if n <= 10 else "tab20")(i % (10 if n <= 10 else 20))


def _legend(axes: list[plt.Axes], title: str) -> None:
    """Legend outside the right panel so it never covers data."""
    axes[1].legend(*axes[0].get_legend_handles_labels(), title=title, fontsize=8,
                   loc="upper left", bbox_to_anchor=(1.02, 1.0), borderaxespad=0)


def _figure(title: str) -> tuple[plt.Figure, list[plt.Axes]]:
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.4), sharex=True)
    fig.suptitle(title, fontsize=13, fontweight="bold")
    axes[0].set_ylabel("CAD / tonne")
    axes[1].set_ylabel("CAD / bushel")
    for ax in axes:
        ax.grid(True, alpha=0.3)
    return fig, list(axes)


def _empty(fig: plt.Figure, axes: list[plt.Axes], message: str) -> None:
    for ax in axes:
        ax.set_yticks([])
        ax.text(0.5, 0.5, message, transform=ax.transAxes, ha="center", va="center",
                fontsize=10, color="dimgray")


def _save(fig: plt.Figure, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return path


def plot_canola_basis(frame: pd.DataFrame, path: Path, coverage: str = "") -> Path:
    """Chart 1: one line per year of daily mean NOV basis, x = day of October."""
    fig, axes = _figure(CANOLA_TITLE)
    for ax in axes:
        ax.set_xlim(0.5, 15.5)
        ax.set_xticks(range(1, 16, 2))
        ax.set_xlabel("October day")
        ax.axhline(0, color="black", linewidth=0.8)

    if frame.empty:
        _empty(fig, axes, f"No canola NOV quotes for Oct 1–15 yet.\n{coverage}")
        return _save(fig, path)

    frame = frame.assign(price_date=pd.to_datetime(frame["price_date"]))
    groups = list(frame.groupby(frame["price_date"].dt.year))
    for i, (year, part) in enumerate(groups):
        color = _color(i, len(groups))
        days = part["price_date"].dt.day
        label = f"{year} (Nov {year} delivery)"
        axes[0].plot(days, part["basis_mt"], marker="o", color=color, label=label)
        axes[1].plot(days, part["basis_bu"], marker="o", color=color, label=label)
    _legend(axes, "Year")
    fig.text(0.01, 0.01,
             f"Mean across quoting PDQ zones, latest import per day. "
             f"{frame['price_date'].dt.date.nunique()} day(s) plotted.",
             fontsize=8, color="dimgray")
    return _save(fig, path)


def _season_start(d: pd.Timestamp) -> int:
    return d.year if d.month == 12 else d.year - 1


def plot_durum_flat(frame: pd.DataFrame, path: Path, coverage: str = "") -> Path:
    """Chart 2: DEC (solid) vs JAN (dashed) delivery, one colour per season."""
    fig, axes = _figure(DURUM_TITLE)
    ticks = [0, 7, 14, 17, 24, 31]  # Dec 15, 22, 29, Jan 1, 8, 15
    labels = [(date(2001, 12, 15) + timedelta(days=t)).strftime("%b %d") for t in ticks]
    for ax in axes:
        ax.set_xlim(-0.5, 31.5)
        ax.set_xticks(ticks, labels)
        ax.set_xlabel("Day of window")

    if frame.empty:
        _empty(fig, axes, f"No durum DEC/JAN quotes for Dec 15–Jan 15 yet.\n{coverage}")
        return _save(fig, path)

    frame = frame.assign(price_date=pd.to_datetime(frame["price_date"]))
    frame["season"] = frame["price_date"].map(_season_start)
    frame["offset"] = [
        (d - pd.Timestamp(s, 12, 15)).days for d, s in zip(frame["price_date"], frame["season"])
    ]
    seasons = sorted(frame["season"].unique())
    # Style by delivery period relative to the season's Dec 15 start year.
    styles = {("DEC", 0): ("-", "o"), ("JAN", 1): ("--", "s"), ("DEC", 1): (":", "^")}
    for (season, month, dyear), part in frame.groupby(["season", "delivery_month", "delivery_year"]):
        color = _color(seasons.index(season), len(seasons))
        linestyle, marker = styles.get((month, dyear - season), ("-.", "x"))
        label = f"{season}-{str(season + 1)[-2:]} · {month} {dyear}"
        for ax, col in zip(axes, ("price_cad_mt", "price_cad_bu")):
            ax.plot(part["offset"], part[col], linestyle=linestyle, marker=marker,
                    markersize=4, color=color, label=label)
    _legend(axes, "Season · delivery")
    fig.text(0.01, 0.01,
             "Solid = DEC (nearby), dashed = JAN, dotted = next DEC (new crop, quoted in "
             "January). Mean across quoting PDQ zones.",
             fontsize=8, color="dimgray")
    return _save(fig, path)


def build_charts(db: PriceDatabase, out_dir: Path = OUTPUT_DIR) -> list[Path]:
    """Query the database and write both charts into ``out_dir``."""
    coverage = _coverage(db)
    return [
        plot_canola_basis(load_canola_basis(db), out_dir / CANOLA_FILE, coverage),
        plot_durum_flat(load_durum_flat(db), out_dir / DURUM_FILE, coverage),
    ]


def main() -> int:
    """Chart the real database into ``output/``."""
    if not config.DUCKDB_PATH.exists():
        print(f"No database at {config.DUCKDB_PATH}; run `python ingest.py --reload-raw` first.")
        return 1
    with PriceDatabase(read_only=True) as db:
        canola = load_canola_basis(db)
        durum = load_durum_flat(db)
        paths = build_charts(db)
    for path in paths:
        print(f"Saved {path.relative_to(config.PROJECT_ROOT)} ({path.stat().st_size:,} bytes)")
    print(f"Canola basis: {len(canola)} day(s); durum DEC/JAN: {len(durum)} day-series point(s).")
    return 0


# --------------------------------------------------------------------------- #
# Pytest checks (synthetic database; never touches the real one)
# --------------------------------------------------------------------------- #
def _synthetic_db(tmp_path: Path) -> PriceDatabase:
    from pdq_parsing import parse_payload

    def row(zone: str, month: str, commodity: str, ton: float, basis: float) -> dict:
        return {"zone": zone, "zone_id": config.PDQ_ZONE_IDS[zone], "month": month,
                "commodity": commodity, "basis": basis, "basis_bushels": basis / 44.09,
                "ton_price": ton, "bus_price": ton / 36.74,
                "ton_price_prev": ton, "bus_price_prev": ton / 36.74}

    db = PriceDatabase(tmp_path / "viz.duckdb", tmp_path / "parquet").connect()
    days = [datetime(y, 10, d, 14, 30) for y in (2025, 2026) for d in (1, 6, 14)]
    days += [datetime(2025, 12, 16, 14, 30), datetime(2026, 1, 5, 14, 30),
             datetime(2026, 1, 15, 14, 30)]
    for i, stamp in enumerate(days):
        data = [row(z, "NOV", "1 CDA CANOLA", 700 + i, -60 + i) for z in ("PEACE", "NW SASK")]
        data += [row("SW SASK", m, "1 CWAD 13.0", 300 + i + off, 0)
                 for m, off in (("DEC", 0), ("JAN", 4))]
        db.upsert_pdq_records(parse_payload({
            "status": "ok", "prices_imported_at": stamp.strftime("%Y-%m-%d %H:%M"),
            "data": data}).records)
    return db


def test_queries_filter_windows(tmp_path):
    db = _synthetic_db(tmp_path)
    try:
        canola = load_canola_basis(db)
        assert len(canola) == 6 and set(canola["n_zones"]) == {2}
        durum = load_durum_flat(db)
        dates = set(pd.to_datetime(durum["price_date"]).dt.date)
        assert dates == {date(2025, 12, 16), date(2026, 1, 5), date(2026, 1, 15)}
        # DEC quoted in January is next December: kept as its own series.
        dec = durum[durum["delivery_month"] == "DEC"]
        assert set(dec["delivery_year"]) == {2025, 2026}
    finally:
        db.close()


def test_charts_written(tmp_path):
    db = _synthetic_db(tmp_path)
    try:
        paths = build_charts(db, tmp_path / "output")
    finally:
        db.close()
    assert [p.name for p in paths] == [CANOLA_FILE, DURUM_FILE]
    assert all(p.stat().st_size > 10_000 for p in paths)


def test_charts_written_when_empty(tmp_path):
    with PriceDatabase(tmp_path / "empty.duckdb", tmp_path / "parquet") as db:
        paths = build_charts(db, tmp_path / "output")
    assert all(p.exists() and p.stat().st_size > 0 for p in paths)


if __name__ == "__main__":
    sys.exit(main())
