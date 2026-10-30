"""StatCan release-table pipeline: parsing rules and load behaviour on synthetic tabs."""

from datetime import date

import duckdb
import pytest

import config
import extractor
import loader
import transformer as tr

PLANTING = config.TOPICS["planting"]
PRODUCTION = config.TOPICS["production"]


@pytest.mark.parametrize("name, code, month, year", [
    ("Mar2015", "Mar2015", "March", 2015), ("March2021", "Mar2021", "March", 2021),
    ("June2026", "Jun2026", "June", 2026), ("2022July", "Jul2022", "July", 2022), ("Dec2019", "Dec2019", "December", 2019),
])
def test_parse_release(name, code, month, year):
    r = tr.parse_release(name)
    assert (r.code, r.month_name, r.year, r.date) == (code, month, year, date(year, r.month, 1))


@pytest.mark.parametrize("name", ["<--Seeded", "Main", "Sheet1", "Foo2021"])
def test_non_release_names(name):
    assert tr.parse_release(name) is None


@pytest.mark.parametrize("header, year, prelim, comparison", [
    ("\xa02021{1} {p}", 2021, True, False), ("2018{1}{p}", 2018, True, False),
    ("March 20151p", 2015, True, False), ("June 2015{1} {p}", 2015, True, False),
    (2019, 2019, False, False), ("\xa02024", 2024, False, False),
    ("2019 to 2020", None, False, True), ("2014 to March 2015", None, False, True), ("% change", None, False, True),
    (None, None, False, False),
])
def test_clean_year_header(header, year, prelim, comparison):
    h = tr.clean_year_header(header)
    assert (h.year, h.is_preliminary, h.is_comparison) == (year, prelim, comparison)


@pytest.mark.parametrize("label, expected", [
    ("Total  wheat (including winter wheat remaining){2}", "Total wheat (including winter wheat remaining)"),
    ("Total wheat (including winter wheat remaining)2", "Total wheat (including winter wheat remaining)"),
    ("Winter wheat3", "Winter wheat"), ("Winter wheat{3}", "Winter wheat"),
    ("Fall Rye", "Fall rye"), ("Fall rye3", "Fall rye"), ("\xa0\xa0  Durum wheat", "Durum wheat"),
    ("Corn for grain", "Corn for grain"), ("", None), (None, None),
])
def test_normalize_crop_name(label, expected):
    assert tr.normalize_crop_name(label) == expected


def test_select_sheets_uses_markers_and_filters():
    names = ["Main", "Dec2022", "2022July", "<--Production", "Mar2014", "Mar2015", "June2026", "Sep2020",
             "<--Seeded", "Mar2027"]
    picked = [d.name for d in extractor.select_sheets(names, PLANTING) if d.selected]
    assert picked == ["Mar2015", "June2026"]       # 2014 too early, Sep not a planting month, Mar2027 after marker
    prod = [d.name for d in extractor.select_sheets(names, PRODUCTION) if d.selected]
    assert prod == ["Dec2022", "2022July"]


def test_select_sheets_rejects_duplicate_releases():
    with pytest.raises(ValueError, match="more than one tab"):
        extractor.select_sheets(["<--Production", "Mar2021", "March2021", "<--Seeded"], PLANTING)


# A 2015-style tab (header in row 1, glued footnotes) and a 2019+ style tab (title row, braces)
TAB_2015 = [
    (None, 2013, 2014, "March 20151p", "2013 to 2014", "2014 to March 2015"),
    (None, "thousands of acres", "thousands of acres", "thousands of acres", "% change", "% change"),
    ("Canola", 19941, 20325, 19416, 1.9, -4.5),
    ("Winter wheat3", 2008, 1675, 1261, -16.6, -24.7),
    ("Lentils", 2620, "x", 3350, None, None),
    ("1. Footnote text", None, None, None, None, None),
]
TAB_2026 = [
    ("Table\xa01\xa0– Estimates of principal field crop areas",),
    ("\xa0", "\xa02024", "\xa02025", "\xa02026{1} {p}", "\xa02024 to 2025", "\xa02025 to 2026", None, None, "stray"),
    (None, "thousands of acres", "thousands of acres", "thousands of acres", "% change", "% change"),
    ("Canola", 22011, 21623, 23442, -1.8, 8.4, None, None, "Canola"),
    ("Fall Rye", 339, 464, 339, 36.8, -26.9),
    ("Source(s):",),
]


def test_transform_2015_layout():
    df = tr.transform_sheet("Mar2015", TAB_2015, tr.parse_release("Mar2015"), PLANTING)
    assert set(df["crop_name"]) == {"Canola", "Winter wheat", "Lentils"}
    assert sorted(df["target_crop_year"].unique()) == [2013, 2014, 2015]
    assert len(df) == 8                                              # Lentils 2014 'x' skipped
    c15 = df[(df["crop_name"] == "Canola") & (df["target_crop_year"] == 2015)].iloc[0]
    assert c15["value"] == 19416 and c15["is_preliminary"] and c15["release_code"] == "Mar2015"


def test_transform_2026_layout_ignores_comparison_and_stray_columns():
    df = tr.transform_sheet("June2026", TAB_2026, tr.parse_release("June2026"), PLANTING)
    assert len(df) == 6 and set(df["crop_name"]) == {"Canola", "Fall rye"}
    assert df.loc[df["target_crop_year"] == 2026, "is_preliminary"].all()
    assert (df["metric_type"] == "Planting").all() and (df["release_month"] == "June").all()


def test_units_are_scaled_and_checked():
    raw_tonnes = [(None, 2016, 2017), (None, "metric tonnes{1}", "metric tonnes{1}"), ("Canola", 19599000, 21328000)]
    df = tr.transform_sheet("Sep2017", raw_tonnes, tr.parse_release("Sep2017"), PRODUCTION)
    assert df["value"].tolist() == pytest.approx([19599.0, 21328.0])           # -> thousands of tonnes
    with pytest.raises(ValueError, match="expects acres_thousand"):
        tr.transform_sheet("Mar2017", raw_tonnes, tr.parse_release("Mar2017"), PLANTING)


def test_upsert_is_idempotent_and_parquet_matches(tmp_path):
    df = tr.transform_sheet("June2026", TAB_2026, tr.parse_release("June2026"), PLANTING)
    con = duckdb.connect(str(tmp_path / "t.duckdb"))
    loader.upsert(con, df, PLANTING)
    df2 = df.copy()
    df2.loc[df2["crop_name"] == "Canola", "value"] += 1                      # a revised re-run
    loader.upsert(con, df2, PLANTING)
    assert con.execute("SELECT count(*) FROM plantings_estimates").fetchone()[0] == 6
    v = con.execute("SELECT acres_thousand FROM plantings_estimates WHERE crop_name='Canola' "
                    "AND target_crop_year=2026").fetchone()[0]
    assert v == 23443
    out = loader.export_parquet(con, PLANTING, root=tmp_path / "pq")
    n = con.execute(f"SELECT count(*) FROM read_parquet('{out.as_posix()}/*/*.parquet', hive_partitioning=true)").fetchone()[0]
    assert n == 6
    con.close()
