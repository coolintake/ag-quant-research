"""Independent verification of Matrix_FOB_Calculator.xlsx (the workbook's "unit tests").

    python src/verify.py                   # checks the saved workbook
    python src/verify.py --workbook copy.xlsx  # check another copy

1. Load counts   : Data_* tables vs the CSV drops and CAN_Costing_Input.xlsx
2. Calc recompute: every Calc row recomputed in Python from the same inputs (PDQ drop, costing,
                   FX, Manual_Inputs, Config rules) and compared with Excel's saved values (+/- 0.005)
3. Error scan    : no Excel error values on Calc / FOB_Summary / Basis
4. History check : sample rows of history_chart.csv recomputed straight from DuckDB + BoC FX
5. World tabs    : Data_World / Data_Freight vs the Hammersmith CSV drops; World_FOB_Matrix spreads and
                   Global_CIF_Matrix landed costs recomputed from the drops + Calc (+/- 0.005)
Exit code 1 if anything fails. Reads the saved file only (Excel must have calculated and saved it).
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl import load_workbook

import config
import layout as L
from fob_common import cost_table, fx_history, fx_on, read_costing, read_pdq, window_months

log = logging.getLogger("verify")
TOL = 0.005
ERRORS = ("#N/A", "#VALUE!", "#REF!", "#DIV/0!", "#NAME?", "#NUM!", "#NULL!", "#SPILL!", "#CALC!")


def num(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


class Report:
    def __init__(self):
        self.fails: list[str] = []

    def check(self, ok: bool, label: str, detail: str = "") -> None:
        (log.info if ok else log.error)("[%s] %s%s", "PASS" if ok else "FAIL", label, f" - {detail}" if detail else "")
        if not ok:
            self.fails.append(label)


def table_rows(wb, sheet: str) -> int:
    ws = wb[sheet]
    return sum(1 for r in ws.iter_rows(min_row=2, max_col=1, values_only=True) if r[0] not in (None, ""))


def expected_manual(man_rows, as_of: date, window: list[str]) -> dict[str, float]:
    """Latest valid manual price (CAD/MT) per crop|zone|ym, applying the workbook's documented rules."""
    labels = {pd.Timestamp(ym + "-01").strftime("%b-%y") + " delivery": ym for ym in window}
    best: dict[str, tuple] = {}
    for r in man_rows:
        d, crop, zone, month, price, unit = r[:6]
        if None in (d, crop, zone, month, price, unit):
            continue
        ym = pd.Timestamp(month).strftime("%Y-%m") if not isinstance(month, str) else labels.get(month)
        if ym not in window:
            continue
        bu = config.CROP_RULES.get(crop, {}).get("bu")
        if not isinstance(price, (int, float)) or bu is None:
            continue
        cad_mt = price * bu if unit == "CAD/bu" else price
        key = f"{crop}|{zone}|{ym}"
        d = pd.Timestamp(d)
        if key not in best or d >= best[key][0]:
            best[key] = (d, cad_mt)
    return {k: v[1] for k, v in best.items()}


def verify_calc(rep: Report, wb, meta: dict) -> None:
    as_of = pd.Timestamp(meta["as_of"]).date()
    usdcad = float(meta["usdcad"])
    window = window_months(as_of)
    pdq = pd.read_csv(config.PDQ_LATEST_CSV).set_index("key")
    costs = cost_table(read_costing())
    man_rows = list(wb[L.MANUAL].iter_rows(min_row=L.MAN_FIRST, max_row=L.MAN_LAST, max_col=6, values_only=True))
    manual = expected_manual(man_rows, as_of, window)
    calc = list(wb[L.CALC].iter_rows(min_row=L.CALC_FIRST, max_row=L.CALC_LAST, max_col=31, values_only=True))
    route_code = {r["route"]: r["code"] for r in config.ROUTES}
    bad, checked = [], 0
    for r in calc:
        crop, zone, route, k = r[0], r[1], r[2], int(r[3])
        rule = config.CROP_RULES[crop]
        ym = window[k - 1]
        allowed = rule["zones"] == "ALL" or zone in rule["zones"].split(";")
        pkey = f"{rule['pdq']}|{zone}|{ym}"
        pdq_cash = float(pdq.at[pkey, "cash_cad_mt"]) if rule["pdq"] and pkey in pdq.index else None
        man = manual.get(f"{crop}|{zone}|{ym}", manual.get(f"{crop}|ALL|{ym}"))
        if not allowed:
            src, cash = "Excluded", None
        elif pdq_cash is not None:
            src, cash = "PDQ", pdq_cash
        elif man is not None:
            src, cash = "Manual", man
        else:
            src, cash = "N/A", None
        cost = costs.get((rule["cost"], zone, route_code[route]), np.nan)
        fob_cad = cash + cost if cash is not None and not np.isnan(cost) else None
        fob_usd = fob_cad / usdcad if fob_cad is not None else None
        fut = None
        if rule["basis"] != "NONE" and pkey in pdq.index and not pd.isna(pdq.at[pkey, "futures_px"]):
            fut = float(pdq.at[pkey, "futures_px"])
        basis = None
        if fut is not None and fob_cad is not None:
            basis = fob_usd / rule["bu"] - fut if rule["basis"] == "USD_BU" else fob_cad - fut
        exp = {"source": src, "fob_cad": fob_cad, "fob_usd": fob_usd,
               "fob_bu": fob_usd / rule["bu"] if fob_usd is not None else None, "basis": basis}
        got = {"source": r[15], "fob_cad": num(r[23]), "fob_usd": num(r[24]), "fob_bu": num(r[25]),
               "basis": num(r[28])}
        checked += 1
        for f in exp:
            e, g = exp[f], got[f]
            ok = (e == g) if f == "source" else ((e is None and g is None) or
                                                 (e is not None and g is not None and abs(e - g) <= TOL))
            if not ok:
                bad.append(f"{crop}|{zone}|{route}|{k} {f}: excel={g} python={e}")
    rep.check(not bad, f"Calc recompute ({checked} rows x 5 fields)", "; ".join(bad[:5]) + (" ..." if len(bad) > 5 else ""))


def verify_world(rep: Report, wb, meta: dict) -> None:
    """World drops loaded, and the selected World_FOB_Matrix / Global_CIF_Matrix views recomputed in Python."""
    import wire_world
    hc = wire_world.hc
    world, freight = pd.read_csv(hc.WORLD_FOB_CSV), pd.read_csv(hc.WORLD_FREIGHT_CSV)
    for sheet, n in ((L.D_WORLD, len(world)), (L.D_FRT, len(freight))):
        got = table_rows(wb, sheet)
        rep.check(got == n, f"{sheet} rows", f"workbook {got} vs source {n}")
    for sheet in wire_world.VIEW_TABS:
        errs = [c for row in wb[sheet].iter_rows(values_only=True) for c in row if isinstance(c, str) and c in ERRORS]
        rep.check(not errs, f"no Excel errors on {sheet}", f"{len(errs)} error cells")
    calc = {r[4]: (num(r[24]), r[15]) for r in wb[L.CALC].iter_rows(min_row=L.CALC_FIRST, max_row=L.CALC_LAST,
                                                                     max_col=31, values_only=True)}
    wfob = {(r.series_id, r.delivery_ym): r.fob_usd_mt for r in world.itertuples() if not pd.isna(r.fob_usd_mt)}
    pref = freight[freight.cif_pref == 1]
    frt = {(r.origin_key, r.destination): r.freight_usd_mt for r in pref.itertuples()}

    def close(a, b):
        return (a is None and b in (None, "N/A", "")) or (a is not None and num(b) is not None and abs(a - num(b)) <= TOL)

    def grid(sheet):
        rows = list(wb[sheet].iter_rows(min_row=1, max_row=50, max_col=17, values_only=True))
        return lambda a: rows[int(a[1:]) - 1][ord(a[0]) - 65] if int(a[1:]) <= len(rows) and ord(a[0]) - 65 < len(
            rows[int(a[1:]) - 1]) else None

    cell = grid(L.W_MTX)
    k, uf, ym, zone = int(cell("M4")), float(cell("O4")), cell("Q4"), cell("D4")
    series = [cell(f"{chr(65 + c)}6") for c in range(1, 1 + wire_world.WORLD_SLOTS)]
    bad, n = [], 0
    for r in range(30, 30 + wire_world.CANADA_SLOTS):
        crop, route = cell(f"M{r}"), cell(f"N{r}")
        if not crop:
            continue
        cn = calc.get(f"{crop}|{zone}|{route}|{k}", (None, ""))[0]
        for j, s in enumerate(series):
            if not s:
                continue
            w = wfob.get((s, ym))
            exp = (cn - w) * uf if cn is not None and w is not None else None
            got = cell(f"{chr(66 + j)}{r}")
            n += 1
            if not close(exp, got):
                bad.append(f"{crop} {route} vs {s}: excel={got} python={exp}")
    rep.check(not bad, f"World_FOB_Matrix spreads ({n} cells, {zone}, {ym})", "; ".join(bad[:4]))

    cell = grid(L.W_CIF)
    k, ym, zone, dest = int(cell("M4")), cell("N4"), cell("F4"), cell("D4")
    proxy = dict(hc.CANADA_FREIGHT_PROXY)
    bad, n = [], 0
    for r in range(12, 12 + wire_world.CANADA_SLOTS + wire_world.WORLD_SLOTS):
        ident, route = cell(f"M{r}"), cell(f"N{r}")
        if not ident:
            continue
        if r < 12 + wire_world.CANADA_SLOTS:
            fob, fkey = calc.get(f"{ident}|{zone}|{route}|{k}", (None, ""))[0], proxy.get(route, "")
        else:
            fob, fkey = wfob.get((ident, ym)), hc.SERIES_META.get(ident, ("", ""))[1]
        fr = frt.get((fkey, dest)) if fkey else None
        exp = fob + fr if fob is not None and fr is not None else None
        n += 1
        if not (close(fob, cell(f"B{r}")) and close(fr, cell(f"E{r}")) and close(exp, cell(f"F{r}"))):
            bad.append(f"{ident} {route or ''}: excel FOB/freight/CIF={cell(f'B{r}')}/{cell(f'E{r}')}/"
                       f"{cell(f'F{r}')} python={fob}/{fr}/{exp}")
    rep.check(not bad, f"Global_CIF_Matrix landed cost ({n} rows, {dest}, {ym})", "; ".join(bad[:3]))


def verify_history(rep: Report) -> None:
    hist = pd.read_csv(config.HISTORY_CSV)
    costs = cost_table(read_costing())
    fx, _ = fx_history(offline=True)
    samples = [("CWRS", "NE SASK"), ("Canola", "SW SASK"), ("Durum", "S ALTA"), ("CPSR", "S ALTA")]
    bad, n = [], 0
    rng = np.random.default_rng(7)
    for crop, zone in samples:
        sub = hist[(hist.crop == crop) & (hist.zone == zone) & (hist.horizon == 0) & (hist.cost_basis == "pro-forma")]
        for _, h in sub.iloc[rng.choice(len(sub), size=min(3, len(sub)), replace=False)].iterrows():
            week = pd.Timestamp(h.week)
            raw = read_pdq((week - pd.Timedelta(days=6)).date(), week.date())
            raw = raw[(raw.pdq_code == config.CROP_RULES[crop]["pdq"]) & (raw.zone == zone)
                      & (raw.delivery_ym == h.delivery_ym)]
            raw = raw[raw.price_date == raw.price_date.max()]
            if raw.empty:
                bad.append(f"{crop} {zone} {h.week}: no DuckDB row")
                continue
            row = raw.iloc[0]
            usdcad, _ = fx_on(fx, row.price_date.date())
            cost = costs[(config.CROP_RULES[crop]["cost"], zone, "VC")]
            exp = (row.ton_price + cost) / usdcad
            n += 1
            if abs(exp - h.fob_vc_usd_mt) > 0.011 or abs(row.ton_price - h.cash_cad_mt) > 0.005:
                bad.append(f"{crop} {zone} {h.week}: csv fob {h.fob_vc_usd_mt} vs {exp:.2f}")
    rep.check(not bad, f"history_chart.csv vs DuckDB ({n} sampled rows)", "; ".join(bad[:4]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workbook", type=Path, default=config.WORKBOOK)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    rep = Report()
    wb = load_workbook(args.workbook, data_only=True, read_only=True)
    try:
        hdr = [c.value for c in next(wb[L.D_FX].iter_rows(min_row=1, max_row=1))]
        meta = dict(zip(hdr, [c.value for c in next(wb[L.D_FX].iter_rows(min_row=2, max_row=2))]))
        rep.check(meta.get("as_of") is not None, "Data_FX loaded", f"as-of {meta.get('as_of')}")
        exp_counts = {L.D_PDQ: len(pd.read_csv(config.PDQ_LATEST_CSV)), L.D_FX: 1,
                      L.D_COST: len(read_costing()), L.D_HIST: len(pd.read_csv(config.HISTORY_CSV))}
        for sheet, n in exp_counts.items():
            got = table_rows(wb, sheet)
            rep.check(got == n, f"{sheet} rows", f"workbook {got} vs source {n}")
        drop_asof = pd.read_csv(config.FX_CSV).as_of.iloc[0]
        rep.check(str(meta.get("as_of"))[:10] == drop_asof, "workbook refreshed from current drop",
                  f"workbook as-of {str(meta.get('as_of'))[:10]} vs fx.csv {drop_asof} (run Refresh All)")
        for sheet in (L.CALC, L.FOB, L.BASIS, L.CONTROL):
            errs = [c for row in wb[sheet].iter_rows(values_only=True) for c in row if isinstance(c, str) and c in ERRORS]
            rep.check(not errs, f"no Excel errors on {sheet}", f"{len(errs)} error cells")
        if meta.get("as_of") is not None:
            verify_calc(rep, wb, meta)
            verify_world(rep, wb, meta)
    finally:
        wb.close()
    verify_history(rep)
    log.info("%s: %d check(s) failed", "VERIFY FAILED" if rep.fails else "VERIFY PASSED", len(rep.fails))
    return 1 if rep.fails else 0


if __name__ == "__main__":
    sys.exit(main())
