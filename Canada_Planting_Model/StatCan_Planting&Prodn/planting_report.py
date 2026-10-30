"""Planting report: what moves between StatCan's March and June seeded-area releases.

March is farmers' first intentions; June is the revised survey taken once most crops are in the
ground. The gap between the two is where the season's acreage story is told (e.g. canola clawing
back acres from cereals). Built for fast signal reading: absolute million-acre shifts, % revision
and a one-word signal tag; no statistical overhead. Reads ``plantings_estimates`` (built by
``main.py run``) and writes a workbook in The Desk house style (Design/DesignSkills.md):

    Summary              3-bullet narrative; every crop's March -> June revision ranked; Wheat ex-Durum
                         and principal-crop total rows; chart
    <Crop>               the focus crop's March vs June, season by season, with charts
    Rotation             acres reshuffled per season: turnover ratio, donor-vs-recipient dominance,
                         shuffle type and spring conditions (from spring_conditions.csv)
    Revision History     each crop's June-revision track record and the season matrix (Mln Ac)
    Ref - Accuracy       March vs June error against the latest revised estimate (reference)
    Ref - Signal Flips   crop-years where June reversed March's year-on-year direction (reference)
    Notes                definitions

Run (from this folder):
    python planting_report.py                          # latest season, focus crop Canola
    python planting_report.py --year 2024 --crop Lentils
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference

import config
import desk_style as ds
from loader import connect

TABLE = config.TOPICS["planting"].table
REPORT_DIR = config.PROJECT_ROOT / "reports"
SPRING_CONDITIONS = config.PROJECT_ROOT / "spring_conditions.csv"

GROUPS = {
    "Cereals": ["Spring wheat", "Durum wheat", "Winter wheat", "Barley", "Oats", "Fall rye", "Corn for grain"],
    "Oilseeds": ["Canola", "Soybeans", "Flaxseed", "Mustard seed", "Sunflower seed"],
    "Pulses & specialty": ["Dry field peas", "Lentils", "Chick peas", "Dry beans", "Canary seed"],
    "Land use": ["Summerfallow"],
}
GROUP_OF = {crop: g for g, crops in GROUPS.items() for crop in crops}
NOT_CROPPED = {"Summerfallow"}
# Derived line items (sum of classes). Reported alongside the classes, never added into totals.
WHEAT_EX_DURUM = "Wheat ex-Durum"
SUBTOTALS = {WHEAT_EX_DURUM: ["Spring wheat", "Winter wheat"]}
GROUP_OF[WHEAT_EX_DURUM] = "Cereals"
# March releases print total wheat incl. winter wheat *seeded in the fall*, June incl. winter wheat
# *remaining*, so StatCan's totals are not comparable; wheat is reported by class plus Wheat ex-Durum.
EXCLUDED_PREFIX = "Total wheat"

MATERIAL = 0.02             # a June revision of at least +/-2% is tagged Gained / Cut
SWAP_BAND = (2 / 3, 1.5)    # dominance ratio inside this band = a one-for-one (binary) swap

# Primary-table column names (trader layout)
C_CROP, C_GROUP, C_MAR, C_JUN = "Crop", "Group", "March (Mln Ac)", "June (Mln Ac)"
C_NET, C_PCT, C_TAG = "Net Revision (Mln Ac)", "Revision (%)", "Signal Tag"
K = 1000.0   # thousand acres -> Mln Ac


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #
def load_estimates() -> pd.DataFrame:
    con = connect(read_only=True)
    try:
        return con.execute(f"""SELECT release_code, release_date, release_month, release_year, crop_name,
                                      target_crop_year, acres_thousand FROM {TABLE}""").df()
    finally:
        con.close()


def load_spring_conditions(path: Path = SPRING_CONDITIONS) -> dict[int, str]:
    """Year -> spring-conditions flag, from the analyst-maintained CSV (missing file or blank = 'Not set')."""
    if not path.exists():
        return {}
    d = pd.read_csv(path, dtype={"conditions": str}).dropna(subset=["conditions"])
    return {int(y): c.strip() for y, c in zip(d["year"], d["conditions"]) if c.strip()}


def _by_month(d: pd.DataFrame, suffix: str) -> pd.DataFrame:
    w = d.pivot_table(index=["crop_name", "target_crop_year"], columns="release_month",
                      values="acres_thousand", aggfunc="first")
    return w.reindex(columns=["March", "June"]).rename(columns={"March": f"march{suffix}", "June": f"june{suffix}"})


def build_panel(df: pd.DataFrame) -> pd.DataFrame:
    """One row per crop x crop year T (thousand acres):
    march / june            the estimate of T in the March T and June T releases (own-year, preliminary)
    march_prev / june_prev  year T-1 as reported in those same releases (drives the Flipped tag)
    revised                 the latest estimate of T from a later year's release (June T+2 at most)"""
    own = df[df["target_crop_year"] == df["release_year"]]
    prior = df[df["target_crop_year"] == df["release_year"] - 1].assign(
        target_crop_year=lambda d: d["target_crop_year"] + 1)
    later = df[df["release_year"] > df["target_crop_year"]].sort_values("release_date")
    revised = later.groupby(["crop_name", "target_crop_year"]).agg(
        revised=("acres_thousand", "last"), revised_release=("release_code", "last"))
    p = (_by_month(own, "").join(_by_month(prior, "_prev"), how="left").join(revised, how="left")
         .reset_index().rename(columns={"crop_name": "crop", "target_crop_year": "year"}))
    p = p[~p["crop"].str.startswith(EXCLUDED_PREFIX)]
    p = pd.concat([p, *[_subtotal(p, name, parts) for name, parts in SUBTOTALS.items()]], ignore_index=True)
    p["group"] = p["crop"].map(GROUP_OF).fillna("Other")
    p["rev"] = p["june"] - p["march"]
    p["rev_pct"] = p["june"] / p["march"] - 1
    p["march_yoy"] = p["march"] / p["march_prev"] - 1
    p["june_yoy"] = p["june"] / p["june_prev"] - 1
    p["march_err"] = (p["march"] / p["revised"] - 1).abs()
    p["june_err"] = (p["june"] / p["revised"] - 1).abs()
    p["tag"] = p.apply(_tag, axis=1)
    return p.sort_values(["crop", "year"]).reset_index(drop=True)


def _subtotal(p: pd.DataFrame, name: str, parts: list[str]) -> pd.DataFrame:
    """Sum of class rows per year; a value is kept only when every class reports it."""
    cols = ["march", "june", "march_prev", "june_prev", "revised"]
    d = p[p["crop"].isin(parts)]
    s = d.groupby("year")[cols].sum(min_count=len(parts))
    s["revised_release"] = d.groupby("year")["revised_release"].first()
    return s.reset_index().assign(crop=name)


def _tag(r) -> str | None:
    if pd.isna(r["rev_pct"]):
        return None
    if r["march_yoy"] < 0 < r["june_yoy"]:
        return "Flipped ▲"
    if r["march_yoy"] > 0 > r["june_yoy"]:
        return "Flipped ▼"
    if r["rev_pct"] >= MATERIAL:
        return "Gained"
    if r["rev_pct"] <= -MATERIAL:
        return "Cut"
    return "Steady"


def _is_line_crop(crops: pd.Series) -> pd.Series:
    """Single crops that add up to the principal-crop area (no summerfallow, no subtotals)."""
    return ~crops.isin(NOT_CROPPED) & ~crops.isin(SUBTOTALS)


# --------------------------------------------------------------------------- #
# Analyses
# --------------------------------------------------------------------------- #
def season(panel: pd.DataFrame, year: int) -> pd.DataFrame:
    s = panel[(panel["year"] == year)].dropna(subset=["march", "june"])
    return s.sort_values("rev", ascending=False)


def principal_total(s: pd.DataFrame) -> dict:
    c = s[_is_line_crop(s["crop"])]
    t = {k: c[k].sum() for k in ("march", "june")}
    t["rev"] = t["june"] - t["march"]
    t["rev_pct"] = t["june"] / t["march"] - 1
    return t


def revision_history(panel: pd.DataFrame, year: int) -> pd.DataFrame:
    h = panel[(panel["year"] <= year)].dropna(subset=["rev"])
    rows = []
    for crop, d in h.groupby("crop"):
        up, down = d.loc[d["rev"].idxmax()], d.loc[d["rev"].idxmin()]
        share = (d["rev"] > 0).mean()
        rows.append({
            C_CROP: crop, C_GROUP: GROUP_OF.get(crop, "Other"), "Seasons": len(d),
            "Avg Net Revision (Mln Ac)": d["rev"].mean() / K, "Avg Revision (%)": d["rev_pct"].mean(),
            "Up Share": share,
            "Largest Gain (Mln Ac)": up["rev"] / K, "Gain Year": int(up["year"]),
            "Largest Cut (Mln Ac)": down["rev"] / K, "Cut Year": int(down["year"]),
            C_TAG: "June adds" if share >= 0.75 else "June cuts" if share <= 0.25 else "Two-sided",
        })
    return pd.DataFrame(rows).sort_values("Avg Net Revision (Mln Ac)", ascending=False)


def accuracy(panel: pd.DataFrame, year: int) -> tuple[pd.DataFrame, dict]:
    a = panel[(panel["year"] <= year)].dropna(subset=["march", "june", "revised"])
    rows = [{C_CROP: crop, "Seasons": len(d), "March Error (%)": d["march_err"].mean(),
             "June Error (%)": d["june_err"].mean(), "June Closer": (d["june_err"] < d["march_err"]).mean()}
            for crop, d in a.groupby("crop")]
    single = a[~a["crop"].isin(SUBTOTALS)]
    pooled = {"n": len(single), "march": single["march_err"].mean(), "june": single["june_err"].mean(),
              "closer": (single["june_err"] < single["march_err"]).mean()}
    return pd.DataFrame(rows).sort_values("June Closer", ascending=False), pooled


def signal_flips(panel: pd.DataFrame, year: int) -> pd.DataFrame:
    f = panel[(panel["year"] <= year) & ~panel["crop"].isin(SUBTOTALS)
              & panel["tag"].fillna("").str.startswith("Flipped")]
    return f.sort_values(["year", "rev"], ascending=[False, False])


def rotation(panel: pd.DataFrame, year: int, spring: dict[int, str]) -> pd.DataFrame:
    c = panel[(panel["year"] <= year) & _is_line_crop(panel["crop"])].dropna(subset=["march", "june"])
    rows = []
    for y, d in c.groupby("year"):
        top, bottom = d.loc[d["rev"].idxmax()], d.loc[d["rev"].idxmin()]
        added, removed = d.loc[d["rev"] > 0, "rev"].sum(), d.loc[d["rev"] < 0, "rev"].sum()
        dominance = top["rev"] / abs(bottom["rev"]) if bottom["rev"] < 0 else np.nan
        rows.append({
            "Year": int(y), "Spring Conditions": spring.get(int(y), "Not set"),
            C_MAR: d["march"].sum() / K, C_JUN: d["june"].sum() / K,
            "Added in June (Mln Ac)": added / K, "Removed in June (Mln Ac)": removed / K,
            C_NET: (added + removed) / K, C_PCT: d["june"].sum() / d["march"].sum() - 1,
            "Turnover Ratio": (added + abs(removed)) / d["march"].sum(),
            "Biggest Gainer": top["crop"], "Gainer (Mln Ac)": top["rev"] / K,
            "Biggest Loser": bottom["crop"], "Loser (Mln Ac)": bottom["rev"] / K,
            "Dominance Ratio": dominance, "Shuffle Type": _shuffle_type(dominance),
        })
    return pd.DataFrame(rows)


def _shuffle_type(ratio: float) -> str:
    if pd.isna(ratio):
        return "No loser"
    if ratio > SWAP_BAND[1]:
        return "Multi-donor"        # the winner drew acres from several crops
    if ratio < SWAP_BAND[0]:
        return "Multi-recipient"    # the loser's acres scattered across several crops
    return "Binary swap"


# --------------------------------------------------------------------------- #
# Narrative (Design/Language_Skills: 3 bullets, qualitative, no figures repeated from the tables)
# --------------------------------------------------------------------------- #
def _outlook(x: float) -> str:
    if pd.isna(x):
        return "an unreported"
    a = abs(x)
    if a < MATERIAL:
        return "a broadly flat"
    size = "a modest" if a < 0.05 else "a solid" if a < 0.10 else "a sharp"
    return f"{size} {'expansion' if x > 0 else 'contraction'}"


def _join(names: list[str]) -> str:
    names = [n.lower() if i else n for i, n in enumerate(names)]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def narrative(panel: pd.DataFrame, s: pd.DataFrame, hist: pd.DataFrame, acc: pd.DataFrame,
              rot: pd.DataFrame, crop: str, year: int) -> list[tuple[str, str]]:
    first = int(panel["year"].min())
    r = s[s["crop"] == crop]
    if r.empty:
        raise SystemExit(f"{crop!r} has no March and June estimate for {year}")
    r = r.iloc[0]
    gained = r["rev"] > 0
    crop_years = panel[(panel["crop"] == crop) & (panel["year"] <= year)].dropna(subset=["rev"])

    # 1. The focus crop
    record = (r["rev"] == crop_years["rev"].max()) if gained else (r["rev"] == crop_years["rev"].min())
    verb = "reversing" if (r["tag"] or "").startswith("Flipped") else ("lifting" if gained else "cutting")
    if gained:
        lead, text = f"{crop} Clawing Back Acres", f"June restored {crop.lower()} area well beyond March intentions"
    else:
        lead, text = f"{crop} Giving Up Acres", f"June trimmed {crop.lower()} area below March intentions"
    text += f", {verb} {_outlook(r['march_yoy'])} outlook versus last year into {_outlook(r['june_yoy'])[2:]}"
    text += (f", the largest June {'upgrade' if gained else 'cut'} in the {first}–{year} record." if record else ".")
    bullets = [(lead, text)]

    # 2. Where the offsetting acres came from (or went), and how they moved
    cropped = s[_is_line_crop(s["crop"]) & (s["crop"] != crop)]
    other = cropped[cropped["rev"] < 0].nsmallest(3, "rev") if gained else cropped[cropped["rev"] > 0].nlargest(3, "rev")
    if len(other):
        grp = (cropped.groupby("group")["rev"].sum().idxmin() if gained
               else cropped.groupby("group")["rev"].sum().idxmax())
        this = rot.set_index("Year").loc[year]
        heaviest = this["Turnover Ratio"] == rot["Turnover Ratio"].max()
        how = {"Multi-donor": "a broad multi-crop shuffle rather than a one-for-one swap",
               "Multi-recipient": "a scattering of acres across several crops",
               "Binary swap": "a near one-for-one swap",
               "No loser": "outright area gains"}[this["Shuffle Type"]]
        action = "gave up the most area" if gained else "picked up the most area"
        bullets.append((f"{grp} {'Ceding Ground' if gained else 'Absorbing Acres'}",
                        f"{_join(other['crop'].tolist())} {action}"
                        f"{' in the most violent reshuffle on record' if heaviest else ''}; "
                        f"{how} drove the move."))

    # 3. How much to trust March for this crop
    h = hist.set_index(C_CROP).loc[crop]
    a = acc.set_index(C_CROP).loc[crop] if crop in set(acc[C_CROP]) else None
    tendency = {"June adds": ("in most seasons", "a floor"), "June cuts": ("in few seasons", "a ceiling"),
                "Two-sided": ("in about half of seasons", "a starting point")}[h[C_TAG]]
    lead = {"June adds": f"March Habitually Understates {crop}", "June cuts": f"March Habitually Overstates {crop}",
            "Two-sided": f"{crop} Revisions Run Both Ways"}[h[C_TAG]]
    closer = ("and usually lands closer to the revised area" if a is not None and a["June Closer"] >= 0.6
              else "though it has not been reliably closer to the revised area")
    bullets.append((lead, f"June has lifted {crop.lower()} above March intentions {tendency[0]} {closer}, "
                          f"so treat March intentions as {tendency[1]}."))
    return bullets


# --------------------------------------------------------------------------- #
# Workbook
# --------------------------------------------------------------------------- #
PCT, AC = ds.FMT_PCT, ds.FMT_ACRES
FMT_X = '0.00"x"'
REF_TAB_COLOR = ds.AVG_GRAY
PRIMARY_FMTS = {C_MAR: AC, C_JUN: AC, C_NET: AC, C_PCT: PCT}


def _primary(d: pd.DataFrame, first_cols: dict) -> pd.DataFrame:
    """The trader layout: label column(s), March, June, Net Revision, Revision (%), Signal Tag."""
    return pd.DataFrame({**first_cols, C_MAR: d["march"] / K, C_JUN: d["june"] / K,
                         C_NET: d["rev"] / K, C_PCT: d["rev_pct"], C_TAG: d["tag"]})


def _bold_row(ws, row: int, values: list, fmts: list):
    """Subtotal line inside a table block: bold, thin rule beneath."""
    for j, (v, fmt) in enumerate(zip(values, fmts)):
        c = ws.cell(row, 1 + j, ds._num(v))
        c.font = ds.F_LABEL_B
        c.alignment = ds.A_LEFT if j < 2 else (ds.A_TEXT if isinstance(v, str) else ds.A_RIGHT)
        c.border = ds.ROW_LINE
        if fmt:
            c.number_format = fmt


def _bar(ws, title: str, y_title: str, y_fmt: str, cat_col: int, val_col: int, first: int, last: int,
         anchor: str, width: float = 24):
    ch = BarChart()
    ch.type = "col"
    ch.add_data(Reference(ws, min_col=val_col, min_row=first - 1, max_row=last), titles_from_data=True)
    ch.set_categories(Reference(ws, min_col=cat_col, min_row=first, max_row=last))
    ds.desk_chart(ch, title, y_fmt, y_title, width=width)
    ds.style_bars(ch)
    ws.add_chart(ch, anchor)


def summary_sheet(wb, s, total, bullets, crop, year):
    ws = ds.sheet(wb, "Summary", f"Canada Seeded Area — March vs June {year}",
                  f"Source: Statistics Canada Table 32-10-0359, March and June releases. Mln Ac = million acres. "
                  f"Focus crop: {crop}.")
    ds.heading(ws, 4, "Key findings")
    row = ds.bullets(ws, 5, bullets, width_cols=7)
    ds.heading(ws, row, f"{year}: March intentions vs June estimate, ranked by net revision")
    crops = s[~s["crop"].isin(SUBTOTALS)]
    t = _primary(crops, {C_CROP: crops["crop"], C_GROUP: crops["group"]})
    top = row + 1
    end = ds.table(ws, top, t, PRIMARY_FMTS, label_cols=2, bold_rows={i for i, c in enumerate(t[C_CROP]) if c == crop})
    fmts = [None, None, AC, AC, AC, PCT, None]
    w = s[s["crop"] == WHEAT_EX_DURUM]
    if len(w):
        w = w.iloc[0]
        _bold_row(ws, end - 1, [f"{WHEAT_EX_DURUM} (Spring + Winter)", "Cereals", w["march"] / K, w["june"] / K,
                                w["rev"] / K, w["rev_pct"], w["tag"]], fmts)
    ds.check_row(ws, end, ["Principal crops (excl. summerfallow)", None, total["march"] / K, total["june"] / K,
                           total["rev"] / K, total["rev_pct"]], fmts[:6])
    _bar(ws, f"Net Revision to March Intentions by Crop, {year} (Mln Ac)", "MLN AC", AC,
         cat_col=1, val_col=5, first=top + 1, last=top + len(t), anchor=f"A{end + 2}", width=22)
    ds.autofit(ws, skip_rows=top - 1, fixed={"A": 36, "B": 18, "G": 14})
    ds.fit_bullet_heights(ws, range(5, 5 + len(bullets)), width_cols=7)


def crop_sheet(wb, panel, crop, year):
    d = panel[(panel["crop"] == crop) & (panel["year"] <= year)].dropna(subset=["march", "june"])
    note = ("Spring wheat + winter wheat. Winter wheat is seeded-in-fall area in March and area remaining in June, "
            "so the June revision includes winterkill." if crop in SUBTOTALS else
            f"Group: {GROUP_OF.get(crop, 'Other')}. Signal Tag definitions on the Notes tab.")
    ws = ds.sheet(wb, crop[:31], f"{crop} — March Intentions vs June Estimate", note)
    t = _primary(d, {"Crop Year": d["year"].astype(int)})
    top = 4
    end = ds.table(ws, top, t, PRIMARY_FMTS | {"Crop Year": "0"})
    ds.check_row(ws, end - 1, [f"June > March: {int((d['rev'] > 0).sum())} of {len(d)} (avg)",
                               None, None, d["rev"].mean() / K, d["rev_pct"].mean()], [None, None, None, AC, PCT])
    first, last = top + 1, top + len(t)

    ch = LineChart()
    for col, (color, width, dash) in {2: (ds.MUTED, 1.5, "lgDash"), 3: (ds.BLACK, 2.5, "solid")}.items():
        ch.add_data(Reference(ws, min_col=col, min_row=top, max_row=last), titles_from_data=True)
        ds.style_line(ch.series[-1], color, width, dash, marker=True)
    ch.set_categories(Reference(ws, min_col=1, min_row=first, max_row=last))
    ds.desk_chart(ch, f"{crop}: March Intentions vs June Estimate (Mln Ac)", AC, "MLN AC", legend=True)
    lo = np.nanmin(d[["march", "june"]].to_numpy()) / K
    ch.y_axis.scaling.min = float(np.floor(lo * 0.95 * 2) / 2)      # start near the data, not at zero
    ws.add_chart(ch, f"A{end + 1}")
    _bar(ws, f"{crop}: Net Revision to March Intentions (Mln Ac)", "MLN AC", AC,
         cat_col=1, val_col=4, first=first, last=last, anchor=f"A{end + 20}")
    ds.autofit(ws, skip_rows=top - 1, fixed={"A": 14})


def rotation_sheet(wb, rot, year):
    ws = ds.sheet(wb, "Rotation", "Acres Reshuffled Between the March and June Reports",
                  "Principal crops excluding summerfallow and subtotals. Turnover = (added + |removed|) / March area. "
                  "Dominance = biggest gainer / |biggest loser|. Spring conditions from spring_conditions.csv.")
    fmts = {c: AC for c in rot.columns if "(Mln Ac)" in c} | {C_PCT: PCT, "Turnover Ratio": PCT,
                                                               "Dominance Ratio": FMT_X, "Year": "0"}
    top = 4
    end = ds.table(ws, top, rot, fmts, label_cols=2, bold_rows={i for i, y in enumerate(rot["Year"]) if y == year})
    first, last = top + 1, top + len(rot)
    col = {c: j + 1 for j, c in enumerate(rot.columns)}
    _bar(ws, "Gross Reshuffle Velocity: Turnover Ratio (% of March Area)", "% OF MARCH AREA", PCT,
         cat_col=1, val_col=col["Turnover Ratio"], first=first, last=last, anchor=f"A{end + 1}", width=20)
    _bar(ws, "Donor vs Recipient: Dominance Ratio (Gainer / |Loser|)", "RATIO", "0.0",
         cat_col=1, val_col=col["Dominance Ratio"], first=first, last=last, anchor=f"H{end + 1}", width=20)
    ds.autofit(ws, skip_rows=top - 1, fixed={"B": 26})


def history_sheet(wb, panel, hist, crop, year):
    ws = ds.sheet(wb, "Revision History", "June Revision to March Intentions — Track Record by Crop",
                  "Up Share = seasons with an upward June revision. Signal Tag: June adds / cuts when one direction "
                  "holds in at least 75% of seasons.")
    fmts = {c: AC for c in hist.columns if "(Mln Ac)" in c} | {"Avg Revision (%)": PCT, "Up Share": ds.FMT_RATIO,
                                                                "Gain Year": "0", "Cut Year": "0"}
    bold = {i for i, c in enumerate(hist[C_CROP]) if c in (crop, *SUBTOTALS)}
    row = ds.table(ws, 4, hist, fmts, label_cols=2, bold_rows=bold)
    ds.heading(ws, row, "Net revision (Mln Ac) by crop and season")
    m = (panel[panel["year"] <= year].pivot_table(index="crop", columns="year", values="rev") / K)
    m = m.reindex(hist[C_CROP]).reset_index().rename(columns={"crop": C_CROP})
    m.columns = [C_CROP] + [str(int(c)) for c in m.columns[1:]]
    ds.table(ws, row + 1, m, {c: AC for c in m.columns[1:]},
             bold_rows={i for i, c in enumerate(m[C_CROP]) if c in (crop, *SUBTOTALS)})
    ds.autofit(ws, skip_rows=3, fixed={"A": 20, "B": 18})


def accuracy_sheet(wb, acc, pooled, year):
    ws = ds.sheet(wb, "Ref - Accuracy", "Reference: Which Release Lands Closer to the Revised Area?",
                  f"Mean absolute % error vs the latest revised estimate, seasons through {year - 1} "
                  f"(the latest season is revised once only).")
    ws.sheet_properties.tabColor = REF_TAB_COLOR
    end = ds.table(ws, 4, acc, {"March Error (%)": PCT, "June Error (%)": PCT, "June Closer": ds.FMT_RATIO})
    ds.check_row(ws, end - 1, ["All single crops (pooled)", pooled["n"], pooled["march"], pooled["june"],
                               pooled["closer"]], [None, "0", PCT, PCT, ds.FMT_RATIO])
    ds.autofit(ws, skip_rows=3, fixed={"A": 26})


def flips_sheet(wb, flips):
    ws = ds.sheet(wb, "Ref - Signal Flips", "Reference: June Reversed March's Year-on-Year Direction",
                  "Prior Year = last season's area as printed in the June release. "
                  "Flipped ▲ = March implied a cut vs last year, June a gain; ▼ the reverse.")
    ws.sheet_properties.tabColor = REF_TAB_COLOR
    t = pd.DataFrame({"Crop Year": flips["year"].astype(int), C_CROP: flips["crop"],
                      "Prior Year (Mln Ac)": flips["june_prev"] / K, C_MAR: flips["march"] / K,
                      C_JUN: flips["june"] / K, C_NET: flips["rev"] / K, C_TAG: flips["tag"]})
    ds.table(ws, 4, t, PRIMARY_FMTS | {"Crop Year": "0", "Prior Year (Mln Ac)": AC}, label_cols=2)
    ds.autofit(ws, skip_rows=3)


def notes_sheet(wb):
    ws = ds.sheet(wb, "Notes", "Definitions and Method")
    ws.sheet_properties.tabColor = REF_TAB_COLOR
    t = pd.DataFrame([
        ("March (Mln Ac)", "Own-year estimate in the March release (survey of planting intentions)."),
        ("June (Mln Ac)", "Own-year estimate in the June release (survey after most seeding is done)."),
        ("Net Revision", "June minus March, in million acres; Revision (%) = June / March − 1."),
        ("Signal Tag", f"Gained / Cut = revision of at least ±{MATERIAL:.0%}; Steady otherwise. Flipped ▲ / ▼ = "
                       "June reversed the year-on-year direction March implied (each vs last year as printed in "
                       "the same release); takes precedence over Gained / Cut."),
        (WHEAT_EX_DURUM, "Spring wheat + winter wheat. Winter wheat is seeded-in-fall area in March and area "
                         "remaining in June, so its revision includes winterkill. Not added into totals."),
        ("Total wheat", "StatCan totals excluded: March and June use different winter wheat definitions."),
        ("Principal crops", "All single crops except summerfallow (subtotals not double-counted)."),
        ("Turnover Ratio", "(Acres added in June + |acres removed|) / March area: how violently crops are swapped."),
        ("Dominance Ratio", f"Biggest gainer / |biggest loser|. Within {SWAP_BAND[0]:.2f}–{SWAP_BAND[1]:.2f}x = "
                            "binary swap; above = multi-donor (winner funded by several crops); below = "
                            "multi-recipient (loser's acres scattered)."),
        ("Spring Conditions", "Analyst flag from spring_conditions.csv (year, conditions, detail, status). "
                              "Entries marked 'Draft - verify' need checking against provincial crop reports; "
                              "edit the CSV and re-run."),
        ("Latest revised", "Most recent later-year release reporting that crop year (up to June T+2); "
                           "used on Ref - Accuracy only."),
    ], columns=["Term", "Definition"])
    ds.table(ws, 3, t, {}, label_cols=2)
    ds.autofit(ws, skip_rows=2, max_width=120)


def write_workbook(path: Path, panel, s, total, bullets, hist, acc, pooled, flips, rot, crop, year) -> Path:
    wb = Workbook()
    wb.remove(wb.active)
    summary_sheet(wb, s, total, bullets, crop, year)
    crop_sheet(wb, panel, crop, year)
    rotation_sheet(wb, rot, year)
    history_sheet(wb, panel, hist, crop, year)
    accuracy_sheet(wb, acc, pooled, year)
    flips_sheet(wb, flips)
    notes_sheet(wb)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        wb.save(path)
    except PermissionError:
        raise SystemExit(f"Cannot write {path}: close it in Excel and re-run.")
    finally:
        wb.close()
    return path


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="March vs June seeded-area report (The Desk style workbook).")
    ap.add_argument("--year", type=int, help="season to report (default: latest with both March and June)")
    ap.add_argument("--crop", default="Canola", help="focus crop for the narrative and crop tab (default Canola)")
    ap.add_argument("--out", type=Path, help="output .xlsx (default reports/Planting_Report_<year>.xlsx)")
    args = ap.parse_args(argv)

    panel = build_panel(load_estimates())
    both = panel.dropna(subset=["march", "june"])
    year = args.year or int(both["year"].max())
    if year not in set(both["year"]):
        raise SystemExit(f"no season with both March and June estimates for {year}")
    crop = next((c for c in panel["crop"].unique() if c.lower() == args.crop.lower()), None)
    if crop is None:
        raise SystemExit(f"unknown crop {args.crop!r}; choose from {sorted(panel['crop'].unique())}")

    spring = load_spring_conditions()
    s = season(panel, year)
    total = principal_total(s)
    hist = revision_history(panel, year)
    acc, pooled = accuracy(panel, year)
    rot = rotation(panel, year, spring)
    bullets = narrative(panel, s, hist, acc, rot, crop, year)
    path = write_workbook(args.out or REPORT_DIR / f"Planting_Report_{year}.xlsx", panel, s, total, bullets, hist,
                          acc, pooled, signal_flips(panel, year), rot, crop, year)

    print(f"\nCanada seeded area — March vs June {year}\n")
    for lead, text in bullets:
        print(f"  • {lead}: {text}")
    view = _primary(s, {C_CROP: s["crop"]})
    print("\n" + view.to_string(index=False, float_format=lambda v: f"{v:,.3f}",
                                formatters={C_PCT: lambda v: f"{v:+.1%}"}))
    print(f"\n  Principal crops: March {total['march'] / K:,.3f} -> June {total['june'] / K:,.3f} Mln Ac "
          f"({total['rev_pct']:+.1%})")
    r = rot.set_index("Year").loc[year]
    print(f"  Rotation: turnover {r['Turnover Ratio']:.1%}, dominance {r['Dominance Ratio']:.2f}x "
          f"({r['Shuffle Type']}), spring conditions: {r['Spring Conditions']}")
    print(f"\nWorkbook: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
