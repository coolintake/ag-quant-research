"""V2 pipeline: prices -> trend yields -> costs -> RPA -> indices -> Plan-A/B -> evaluation -> exports.

    python run_v2.py

Writes (output/): v2_panel.csv (with base_window), v2_plan_b_fits.csv, v2_plan_b_predictions.csv,
v2_evaluation_*.csv, v2_cost_validation.csv, Prairie_RPA_Acreage_Diagnostic_R1/_R2/_R3.xlsx (window-matched
bases, The Desk style), v2_diagnostics.md.
All sources are read-only (StatCan CSV, crop_prices.duckdb, RPA_Calculator budgets).
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import config as C  # noqa: E402
import export_excel  # noqa: E402
from engine import forecast_engine as fe  # noqa: E402
from engine import index_engine as ie  # noqa: E402
from engine import price_engine as pe  # noqa: E402
from engine import rpa_engine as re_  # noqa: E402
from engine import yield_engine as ye  # noqa: E402


def _pct(v):
    return "—" if pd.isna(v) else f"{v:+.1%}"


def _f(v, fmt="{:.1f}"):
    return "—" if pd.isna(v) else fmt.format(v)


def diagnostics_md(panel, fits, preds, ev, prelim, extract, validation) -> str:
    fy = prelim
    md = [f"# V2 diagnostics — relative cash RPA index vs CANSIM ({date.today().isoformat()})", "",
          f"Spec: `SPECIFICATION_V2.md`. CANSIM extract {extract}; {prelim} acres preliminary. Cost lines reproduce "
          f"{len(validation)} Historical_Ratios values (max |diff| {validation['abs_diff'].max():.1e}).", ""]

    md += ["## Signal skill (2020–" + str(prelim) + ", provinces pooled)", "",
           "| Window | Directional hit rate | N | Mean Spearman ρ (across crops) | Province-years |", "|---|---|---|---|---|"]
    sp = ev["spearman"].groupby("run")["rho"].agg(["mean", "count"])
    for _, r in ev["hit_rate"].iterrows():
        s = sp.loc[r["run"]] if r["run"] in sp.index else None
        md.append(f"| {C.WINDOW_LABELS[r['run']]} | {r['hit_rate']:.0%} | {int(r['n'])} | "
                  f"{_f(s['mean'], '{:+.2f}') if s is not None else '—'} | {int(s['count']) if s is not None else 0} |")
    md += ["", "Hit rate by crop (Run 3): " + ", ".join(
        f"{C.CROPS[c]['label']} {v:.0%}" for c, v in ev["hit_by_crop"].set_index("run").loc[C.REFERENCE_WINDOW].items()
        if pd.notna(v)) + ".", ""]

    md += [f"## Plan-B pooled regression through the origin (fit {C.YEARS[0] + 1}–{prelim - 1})", "",
           "%ΔAcres = b × %ΔSignal (intercept forced to 0). Plan-B is the automated fallback / prior behind analyst "
           "Plan-A. R² is centered.", "",
           "| Window | N | b (SE) | p(b) | R² | Flag |", "|---|---|---|---|---|---|"]
    for _, r in fits.iterrows():
        if r["status"] != "OK":
            md.append(f"| {C.WINDOW_LABELS[r['window']]} | {int(r['n'])} | — | — | — | {r['status']} |")
            continue
        md.append(f"| {C.WINDOW_LABELS[r['window']]} | {int(r['n'])} | {r['b']:+.3f} ({r['b_se']:.3f}) | "
                  f"{r['b_p']:.3f} | {r['r2']:.3f} | {r['flag'] or '—'} |")
    md += ["", "Out-of-sample WAPE (fitted on years before T), Plan-B vs no-change:", "",
           "| Window | " + " | ".join(str(y) for y in sorted(ev["wape"]["year"].unique())) + " |",
           "|---|" + "---|" * ev["wape"]["year"].nunique()]
    for run, g in ev["wape"].groupby("window"):
        g = g.set_index("year")
        md.append(f"| {C.WINDOW_SHORT[run]} | " + " | ".join(
            f"{_f(g.loc[y, 'wape_plan_b'] * 100, '{:.1f}')}% vs {_f(g.loc[y, 'wape_nochange'] * 100, '{:.1f}')}%"
            for y in sorted(g.index)) + " |")

    md += ["", f"## {fy} Run 3: relative index (R3 base) and signal vs CANSIM (preliminary)", "",
           "| Geography | Crop | RPA $/ac | RelIndex | % Δ signal | CANSIM % Δ acres | Same direction | Plan-B % Δ | Flags |",
           "|---|---|---|---|---|---|---|---|---|"]
    pb = preds[(preds["year"] == fy) & (preds["window"] == C.REFERENCE_WINDOW)].set_index(["province", "crop"])
    r3 = panel[(panel["run"] == C.REFERENCE_WINDOW) & (panel["year"] == fy)]
    for _, r in r3.iterrows():
        same = "—" if pd.isna(r["pct_d_signal"]) or pd.isna(r["pct_d_acres"]) else (
            "yes" if np.sign(r["pct_d_signal"]) == np.sign(r["pct_d_acres"]) else "no")
        p_b = pb.loc[(r["province"], r["crop"]), "pct_plan_b"] if (r["province"], r["crop"]) in pb.index else np.nan
        md.append(f"| {r['province']} | {C.CROPS[r['crop']]['label']} | {_f(r['rpa'], '{:,.0f}')} | "
                  f"{_f(r['rel_index'])} | {_pct(r['pct_d_signal'])} | {_pct(r['pct_d_acres'])} | {same} | "
                  f"{_pct(p_b)} | {r['flags'] or ''} |")
    md += ["", "Signal for CWRS = RPA_CWRS / RPA_Canola. Plan-A: see `inputs/plan_a_assignments.csv` and the "
           "Evaluation tab of the workbook.", ""]
    return "\n".join(md)


def main() -> int:
    C.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    validation = re_.validate_costs()
    cansim = ye.load_cansim()
    yields = ye.trend_yields(cansim)
    acres, prelim, extract = ye.acreage(cansim)
    costs = re_.load_costs()
    prices = pe.load_prices()
    rpa = re_.compute_rpa(prices, yields, costs)
    panel = ie.build_index_panel(rpa, acres, prelim)

    fits, preds, obs = fe.plan_b(panel, last_actual=prelim - 1, forecast_year=prelim)
    plan_a_rows = fe.apply_plan_a(fe.load_plan_a(), panel)
    ev = fe.evaluate(panel, preds, plan_a_rows)

    panel.to_csv(C.PANEL_CSV, index=False)
    fits.to_csv(C.OUTPUT_DIR / "v2_plan_b_fits.csv", index=False)
    preds.to_csv(C.OUTPUT_DIR / "v2_plan_b_predictions.csv", index=False)
    validation.to_csv(C.OUTPUT_DIR / "v2_cost_validation.csv", index=False)
    for k in ("hit_rate", "hit_by_crop", "spearman", "wape"):
        ev[k].to_csv(C.OUTPUT_DIR / f"v2_evaluation_{k}.csv", index=False)
    books = export_excel.write_workbooks(panel, fits, preds, obs, ev, plan_a_rows, validation, prelim, extract,
                                         forecast_year=prelim)
    C.DIAGNOSTICS_MD.write_text(diagnostics_md(panel, fits, preds, ev, prelim, extract, validation), encoding="utf-8")
    for p in (C.PANEL_CSV, *books, C.DIAGNOSTICS_MD):
        print(f"Wrote {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
