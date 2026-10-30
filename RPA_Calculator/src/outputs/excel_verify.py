"""Recalculate the master workbook in Excel (Windows + Excel + pywin32) and check it.

The workbook is compared against the engine (src/engine/market.py) on identical inputs and
against the golden snapshot (tests/golden/rpa_2026.json; changes logged in its revisions), so the
spreadsheet, the JSON outputs and the engine cannot drift apart.

    python -m src.outputs.excel --verify
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from ..config import PROJECT_ROOT, PROVINCES, RULES, crop, province
from ..engine.market import Scenario, economics, last_yr, run_scenario
from ..engine.prairie_engine import aggregate, provincial_last_yr
from ..normalize.baselines import n_shares
from ..pricing.fertilizer import quotes_from_per_lb
from .excel import AGRO_SHEET, FIRST_COL, S_ROW, ratio_value

PRAIRIE_KEYS = ("yield", "price", "revenue", "seed", "fert", "chem", "cost", "rpa", "rank")

GOLDEN = PROJECT_ROOT / "tests" / "golden" / "rpa_2026.json"
KEYS = ("yield", "price", "revenue", "seed", "fert", "chem", "cost", "rpa", "be_price", "be_yield")


def verify_in_excel(path: Path, layout: dict) -> bool:
    import win32com.client  # noqa: PLC0415

    year, cp_refs, crops = layout["year"], layout["cp"], layout["crops"]
    golden = json.loads(GOLDEN.read_text(encoding="utf-8")) if GOLDEN.exists() and year == 2026 else None
    tmp = Path(tempfile.mkdtemp()) / path.name
    shutil.copy(path, tmp)
    xl = win32com.client.DispatchEx("Excel.Application")
    xl.Visible, xl.DisplayAlerts = False, False
    ok = True

    def report(name: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok &= bool(passed)
        print(f"  [{'PASS' if passed else 'FAIL'}] {name:<62} {detail}")

    try:
        wb = xl.Workbooks.Open(str(tmp))
        cp = wb.Worksheets("Control_Panel")
        xl.CalculateFull()

        def errors() -> list[str]:
            bad = []
            for ws in wb.Worksheets:
                used = ws.UsedRange
                for row in range(1, used.Rows.Count + 1):
                    for col in range(1, used.Columns.Count + 1):
                        text = str(used.Cells(row, col).Text)
                        if text.startswith("#") and not text[1:2].isdigit():
                            bad.append(f"{ws.Name}!{used.Cells(row, col).Address}={text}")
            return bad

        def summary(code: str) -> dict[str, dict]:
            ws = wb.Worksheets(f"RPA_Summary_{code}")
            return {b.crop_key: {k: ws.Cells(S_ROW[k], FIRST_COL + i).Value2 for k in S_ROW}
                    for i, b in enumerate(crops[code])}

        def gap(got: dict, want: dict, keys=KEYS) -> float:
            return max(abs((got[k] or 0) - (want[k] or 0)) for k in keys)

        def set_cell(cell, value) -> None:
            cp.Cells(*cell).Value2 = value

        print(f"\nExcel verification ({tmp.name})")
        bad = errors()
        report(f"no formula errors at Last Yr inputs ({wb.Worksheets.Count} tabs)", not bad, f"{bad[:3]}")
        report("tab order follows AGENT_RULES outputs.workbook_tabs",
               [ws.Name for ws in wb.Worksheets][:len(RULES.workbook_tabs)] == list(RULES.workbook_tabs))
        base = {code: summary(code) for code in PROVINCES}

        def prairie_sheet() -> dict[str, dict]:
            ws, pl = wb.Worksheets("RPA_Prairie"), layout["prairie"]
            col_idx = {k: i for i, k in enumerate(pl["keys"])}
            return {k: {m: ws.Cells(pl["rows"][m], FIRST_COL + col_idx[k]).Value2 for m in PRAIRIE_KEYS}
                    for k in pl["keys"]}

        def prairie_gap(got: dict, want: dict) -> float:
            return max(abs(got[k][m] - want["crops"][k][m]) for k in got for m in PRAIRIE_KEYS)

        worst = prairie_gap(prairie_sheet(), aggregate(provincial_last_yr(year)))
        report(f"RPA_Prairie == Prairie engine at Last Yr ({len(layout['prairie']['keys'])} crops)", worst < 1e-9,
               f"max diff {worst:.1e}")
        pws, pl = wb.Worksheets("RPA_Prairie"), layout["prairie"]
        worst = max(abs(pws.Cells(pl["rows"]["base"], FIRST_COL + i).Value2 - pws.Cells(pl["rows"]["rpa"], FIRST_COL + i).Value2)
                    + abs(pws.Cells(pl["rows"]["delta"], FIRST_COL + i).Value2) for i in range(len(pl["keys"])))
        report("RPA_Prairie: Last Yr RPA == Return and Delta 0 at Last Yr inputs", worst < 1e-9, f"{worst:.1e}")

        for code in PROVINCES:
            spec = province(code)
            worst = max(gap(base[code][b.crop_key], last_yr(b)) for b in crops[code])
            report(f"{code}: workbook == engine at Last Yr (all rows, {len(crops[code])} crops)", worst < 1e-9,
                   f"max diff {worst:.1e}")
            worst = max(abs(v["delta"]) + abs(v["base"] - v["rpa"]) for v in base[code].values())
            report(f"{code}: Last Yr RPA == C − D and delta 0 at Last Yr inputs", worst < 1e-9, f"{worst:.1e}")
            names = [wb.Worksheets(f"RPA_Summary_{code}").Cells(S_ROW["crop"], FIRST_COL + i).Value
                     for i in range(len(crops[code]))]
            agro = wb.Worksheets(AGRO_SHEET)
            rate_names = [agro.Cells(layout["rates"][code][b.crop_key]["row"], 2).Value2 for b in crops[code]]
            bid_names = [cp.Cells(cp_refs[code]["bid_cells"][b.crop_key][0], 2).Value2 for b in crops[code]]
            labels = [b.trade_label for b in crops[code]]
            report(f"{code}: trade labels align (Summary / Rates / Bids)",
                   [n.rstrip("†") for n in names] == rate_names == bid_names == labels, ", ".join(labels))
            zones = {b.crop_key: str(wb.Worksheets(f"RPA_Summary_{code}").Cells(S_ROW["zone"], FIRST_COL + i).Value2)
                     for i, b in enumerate(crops[code])}
            inactive = [n for r, n in spec.regions.items() if r not in spec.active_regions]
            bad_z = [k for k, z in zones.items()
                     if any(n in z for n in inactive)
                     or any(spec.regions[r] not in z.replace("\n", " ") for r in dict.fromkeys(
                         next(b for b in crops[code] if b.crop_key == k).regions))]
            report(f"{code}: regions only from active rules ({', '.join(spec.active_regions)})", not bad_z, f"{bad_z}")
            if golden:
                g = golden["workbook"]["last_yr"][code]
                worst = max(gap(base[code][k], g[k], ("revenue", "cost", "rpa")) for k in g)
                report(f"{code}: C, D, C − D == golden snapshot (Last Yr)", worst < 1e-9,
                       f"max diff {worst:.1e}")

        # ---- golden sample inputs, then engine on the same inputs
        def sample(code: str) -> tuple[dict, dict]:
            inp = golden["sample_inputs"][code]
            quotes = (quotes_from_per_lb(inp["nutrient_per_lb"], province(code)) if "nutrient_per_lb" in inp
                      else inp["quotes"])
            return quotes, {k: {"farm_price": p, "unit": crop(k).price_basis} for k, p in inp["bids"].items()}

        samples = {code: sample(code) for code in PROVINCES}
        for code, (quotes, bids) in samples.items():
            for n, q in quotes.items():
                set_cell(cp_refs[code]["quote_cells"][n], q)
            for k, bid in bids.items():
                set_cell(cp_refs[code]["bid_cells"][k], bid["farm_price"])
        xl.CalculateFull()
        bad = errors()
        report("no formula errors after input changes", not bad, f"{bad[:3]}")
        after = {code: summary(code) for code in PROVINCES}
        runs = {code: run_scenario(Scenario.from_dict({"name": "v", "province": code, "nutrient_quotes": quotes,
                                                       "crop_prices": bids}, year))
                for code, (quotes, bids) in samples.items()}
        for code in PROVINCES:
            eng = {r["crop_key"]: r for r in runs[code]["results"]}
            worst = max(gap(after[code][k], eng[k]["scenario"]) for k in after[code])
            report(f"{code}: workbook == engine after input change", worst < 1e-9, f"max diff {worst:.1e}")
            worst = max(abs(after[code][k]["rank"] - eng[k]["rank"]) for k in after[code])
            report(f"{code}: RANK (ties shared) == engine rank", worst == 0)
            if golden:
                g = golden["workbook"]["sample"][code]
                worst = max(gap(after[code][k], g[k], ("revenue", "cost", "rpa")) for k in g)
                report(f"{code}: C, D, C − D == golden snapshot (sample inputs)", worst < 1e-9,
                       f"max diff {worst:.1e}")

        econ = {code: {r["crop_key"]: r["scenario"] for r in runs[code]["results"]} for code in PROVINCES}
        worst = prairie_gap(prairie_sheet(), aggregate(econ))
        report("RPA_Prairie == Prairie engine after input change", worst < 1e-9, f"max diff {worst:.1e}")

        # ---- [NCREDIT-MB] N blend share: workbook share cell == engine n_shares
        for code in PROVINCES:
            if "share_cell" not in cp_refs[code]:
                continue
            src = next(iter(province(code).n_blend))
            set_cell(cp_refs[code]["share_cell"], 0.6)
            xl.CalculateFull()
            quotes, bids = samples[code]
            eng = {r["crop_key"]: r["scenario"] for r in run_scenario(Scenario.from_dict(
                {"name": "blend", "province": code, "nutrient_quotes": quotes, "crop_prices": bids,
                 "n_shares": {src: 0.6}}, year))["results"]}
            now = summary(code)
            worst = max(gap(now[k], eng[k]) for k in now)
            report(f"{code}: {src} share 60% in workbook == engine n_shares", worst < 1e-9, f"max diff {worst:.1e}")
            set_cell(cp_refs[code]["share_cell"], n_shares(code, year)[src])
            xl.CalculateFull()

        # ---- class inputs (SK CPS): own bid / yield / seed; parent untouched; engine agrees
        for code in PROVINCES:
            for b in (x for x in crops[code] if x.inherits):
                cls = cp_refs[code]["class"][b.crop_key]
                vals = {"price": 7.60, "yield": 64, "seed": 31.00}
                before_parent = summary(code)[b.inherits]
                set_cell(cp_refs[code]["bid_cells"][b.crop_key], vals["price"])
                set_cell(cls["yield_cell"], vals["yield"])
                set_cell(cls["seed_cell"], vals["seed"])
                xl.CalculateFull()
                now = summary(code)
                quotes, bids = samples[code]
                eng = run_scenario(Scenario.from_dict({
                    "name": "cls", "province": code, "nutrient_quotes": quotes,
                    "crop_prices": {**bids, b.crop_key: {"farm_price": vals["price"], "unit": "$/bu"}},
                    "crop_yields": {b.crop_key: vals["yield"]}, "seed_costs": {b.crop_key: vals["seed"]}}, year))
                e = next(r for r in eng["results"] if r["crop_key"] == b.crop_key)["scenario"]
                report(f"{code} {b.trade_label}: class inputs == engine (fert/chem from {b.inherits})",
                       gap(now[b.crop_key], e) < 1e-9, f"RPA ${now[b.crop_key]['rpa']:.2f}")
                report(f"{code} {b.trade_label}: parent {b.inherits} untouched",
                       abs(now[b.inherits]["rpa"] - before_parent["rpa"]) < 1e-9)

        # ---- provinces independent
        snap = {code: summary(code) for code in PROVINCES}
        for code in PROVINCES:
            set_cell(cp_refs[code]["quote_cells"]["N"], cp.Cells(*cp_refs[code]["quote_cells"]["N"]).Value2 * 1.2)
            xl.CalculateFull()
            others = [c for c in PROVINCES if c != code]
            moved = max((abs(summary(o)[k]["rpa"] - snap[o][k]["rpa"]) for o in others for k in snap[o]), default=0)
            report(f"{code} input change leaves other provinces untouched", moved < 1e-9, f"max move {moved:.1e}")
            snap = {c: summary(c) for c in PROVINCES}

        # ---- Historical_Ratios: static ratios == history, Forecast == live summaries
        hr, h = wb.Worksheets("Historical_Ratios"), layout["hist"]
        live = {code: summary(code) for code in PROVINCES}
        bad_static, worst = [], 0.0
        for row in h["rows"]:
            code, num, base = row["province"], row["numerator"], row["base"]
            for j, y in enumerate(h["years"]):
                want = ratio_value(h["history"][code].get(y, {}).get(num), h["history"][code].get(y, {}).get(base))
                got = hr.Cells(row["row"], 4 + j).Value2
                if (want is None and got != "—") or (isinstance(want, str) and got != want) or                         (isinstance(want, float) and abs(got - want) > 1e-9):
                    bad_static.append(f"{code} {num}/{base} {y}")
            got = hr.Cells(row["row"], h["forecast_col"]).Value2
            want = ratio_value(live[code][num]["rpa"], live[code][base]["rpa"])
            worst = max(worst, 0.0 if got == want else abs(got - want) if isinstance(got, float) else 1.0)
        report(f"Historical_Ratios static {h['years'][0]}–{h['years'][-1]} == guide history ({len(h['rows'])} rows)",
               not bad_static, f"{bad_static[:3]}")
        report("Historical Forecast column == live summary ratios", worst < 1e-9, f"{worst:.1e}")
        wb.Close(SaveChanges=False)
    finally:
        xl.Quit()
        shutil.rmtree(tmp.parent, ignore_errors=True)
    print(f"\n{'ALL PASS' if ok else 'FAILURES ABOVE'}")
    return ok
