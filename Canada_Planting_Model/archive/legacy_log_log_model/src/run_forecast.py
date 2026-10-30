"""One-step-forward acreage forecast with the land pool, evaluated against CANSIM (spec 8-9).

    python src/run_forecast.py                       # 2026, Run 3, parameters from the 2025 vintage
    python src/run_forecast.py --year 2026 --run RUN1 --vintage 2025

Steps per province (MB, SK, AB):
  1. Parameters: run-w fits on t <= vintage (= the vintage tab of rpa_elasticity_matrices.xlsx).
  2. Live signal for T: RPA from T trend yields, T costs and window-w prices (spec 4.3 hierarchy),
     ratio vs its own mean over T-5 .. T-1 -> x = ln(R_T / R-bar).
  3. Tier-ordered forecast: T0 CWRS, T1 vs CWRS, T2 vs Durum / Canola:
     A_hat = A_{T-1} * exp(alpha0 + beta * x). Held at T-1 (spec 8.2): EXCLUDED_REGIONAL_CROP,
     UNCALIBRATED, MISSING_PRICE_DATA, NONPOSITIVE_RPA, MISSING_BASELINE.
  4. allocator: rolling 25-year fallow floor and cropland pool; spec 9 flags.
  5. Evaluation vs CANSIM T seeded area, with the no-change (A_{T-1}) benchmark.

Outputs: output/forecast_<T>_<run>.csv, output/land_pool_balance_sheet_<T>.csv,
output/reports/<T>_forecast_vs_cansim.md
"""

from __future__ import annotations

import argparse
import logging
from datetime import date

import numpy as np
import pandas as pd

import allocator
import data_loader
import regression_engine as reg
import settings as S

HOLD_REASONS = ("EXCLUDED_REGIONAL_CROP", "UNCALIBRATED", "MISSING_PRICE_DATA", "NONPOSITIVE_RPA", "MISSING_BASELINE")


def _pct(f, a):
    return (f - a) / a if (pd.notna(a) and a and pd.notna(f)) else np.nan


def pair_forecasts(panel: pd.DataFrame, prov: str, year: int, run: str, vintage: int) -> pd.DataFrame:
    """Parameters and live signal for every pair in one province; growth factor or hold reason."""
    cell = panel[(panel["province"] == prov) & (panel["run"] == run)]
    ratios = reg.build_cell_ratios(cell)
    fits = reg.fit_cell(ratios[ratios["year"] <= vintage], prov, run).set_index("pair")
    live = ratios[ratios["year"] == year].set_index("pair")
    rows = []
    for crop, anchor in S.PAIRS:
        pair = f"{crop}/{anchor}"
        f, x = fits.loc[pair], live.loc[pair]
        rec = {"province": prov, "tier": S.TIERS[(crop, anchor)], "crop": crop, "anchor": anchor, "pair": pair,
               "fit_status": f["status"], "beta_flag": f.get("flag"), "alpha0": f.get("alpha0_bounded"),
               "beta": f.get("beta_bounded"), "fit_n": f.get("n"),
               "price_j": x["price_j"], "price_source_j": x["price_source_j"],
               "price_a": x["price_a"], "price_source_a": x["price_source_a"],
               "exp_yield_j": x["exp_yield_j"], "variable_cost_j": x["variable_cost_j"],
               "rpa_j": x["rpa_j"], "rpa_a": x["rpa_a"], "ratio_live": x["ratio"],
               "ratio_baseline": x["ratio_baseline"], "baseline_n": x["baseline_n"], "x_dev": x["x_dev"]}
        if f["status"] == S.EXCLUDED_STATUS:
            hold = "EXCLUDED_REGIONAL_CROP"
        elif f["status"] not in reg.FIT_OK:
            hold = "UNCALIBRATED"
        elif pd.isna(x["price_j"]) or pd.isna(x["price_a"]):
            hold = "MISSING_PRICE_DATA"
        elif pd.notna(x["rpa_j"]) and pd.notna(x["rpa_a"]) and min(x["rpa_j"], x["rpa_a"]) <= 0:
            hold = "NONPOSITIVE_RPA"
        elif pd.isna(x["x_dev"]):
            hold = "MISSING_BASELINE"
        else:
            hold = None
        rec["action"] = "HOLD_T-1" if hold else "APPLY_ELASTICITY"
        rec["hold_reason"] = hold
        rec["growth_factor"] = 1.0 if hold else float(np.exp(f["alpha0_bounded"] + f["beta_bounded"] * x["x_dev"]))
        rows.append(rec)
    return pd.DataFrame(rows), fits.reset_index()


def check_against_vintage(fits: pd.DataFrame, run: str, vintage: int) -> str:
    """Confirm the refitted parameters equal the saved vintage tab (fits_by_vintage.csv)."""
    path = S.REPORTS_DIR / "fits_by_vintage.csv"
    if not path.exists():
        return "fits_by_vintage.csv not found; run run_calibration.py to cross-check"
    saved = pd.read_csv(path)
    saved = saved[(saved["vintage"] == vintage) & (saved["run"] == run)]
    m = fits.merge(saved, on=["province", "pair"], suffixes=("", "_saved"))
    diff = (m["beta_bounded"] - m["beta_bounded_saved"]).abs().max()
    ok = len(m) == len(fits) and (pd.isna(diff) or diff < 1e-10)
    if not ok:
        raise ValueError(f"Refitted {run} parameters differ from the {vintage} vintage tab (max diff {diff})")
    return f"matches the {vintage}/{vintage + 1} tab of rpa_elasticity_matrices.xlsx ({run}) exactly"


def run(year: int, run_name: str, vintage: int) -> dict:
    data = data_loader.build_panel(years=tuple(range(S.YEARS[0], year + 1)))
    panel, statcan = data["panel"], data["statcan"]
    field = allocator.load_field_area(year - allocator.SF_FLOOR_YEARS - 1)

    forecasts, balance, pools, all_fits, missing_notes = [], [], [], [], []
    for prov in S.PROVINCES:                                               # MB, SK, AB
        pairs, fits = pair_forecasts(panel, prov, year, run_name, vintage)
        all_fits.append(fits.assign(province=prov))
        prev, miss_prev = allocator.basket(field, prov, year - 1)
        actual, miss_act = allocator.basket(field, prov, year)
        missing_notes.append((prov, miss_prev, miss_act))
        lines = prev.drop(columns="published").rename(columns={"acres": "acres_prev"}).merge(
            actual[["line", "acres", "published"]].rename(columns={"acres": "acres_actual"}), on="line", how="left")
        actual_total = float(lines["acres_actual"].sum())          # unpublished series count as 0 in totals
        lines.loc[~lines["published"], "acres_actual"] = np.nan    # ...but show as unpublished per line
        lines = lines.drop(columns="published")
        by_crop = pairs.set_index("crop")
        status, growth = [], []
        for _, ln in lines.iterrows():
            if ln["crop"] in by_crop.index:
                p = by_crop.loc[ln["crop"]]
                growth.append(p["growth_factor"])
                status.append(p["hold_reason"] or "APPLY_ELASTICITY")
            elif ln["crop"] == "CORN" and S.is_excluded(prov, "CORN"):
                growth.append(1.0); status.append(S.EXCLUDED_STATUS)
            else:
                growth.append(1.0); status.append("UNMODELLED_HELD")
        lines["status"] = status
        lines["acres_unconstrained"] = lines["acres_prev"] * np.array(growth)
        lines, pool = allocator.allocate(lines, field, prov, year)
        lines["flags"] = allocator.forecast_flags(lines, statcan, prov, year)
        lines.insert(0, "province", prov)
        pools.append(pool)

        merged = lines.merge(pairs.drop(columns=["province"]), on="crop", how="left")
        merged["variance"] = merged["acres_allocated"] - merged["acres_actual"]
        merged["pct_error"] = [_pct(f, a) for f, a in zip(merged["acres_allocated"], merged["acres_actual"])]
        merged["naive_pct_error"] = [_pct(f, a) for f, a in zip(merged["acres_prev"], merged["acres_actual"])]
        forecasts.append(merged)

        sf_actual = field[(field["province"] == prov) & (field["series"] == allocator.FALLOW)
                          & (field["year"] == year)]["acres"]
        sf_actual = float(sf_actual.iloc[0]) if len(sf_actual) else np.nan
        for _, ln in lines.iterrows():
            balance.append({"province": prov, "section": "crop line", "item": ln["line"], "status": ln["status"],
                            f"acres_{year - 1}": ln["acres_prev"], f"unconstrained_{year}": ln["acres_unconstrained"],
                            f"allocated_{year}": ln["acres_allocated"], f"cansim_{year}": ln["acres_actual"]})
        pool["cropland_actual"] = actual_total
        totals = [("Total cropland", pool["cropland_prev"], pool["unconstrained_total"], pool["allocated_total"],
                   actual_total),
                  ("Summer fallow", pool["summerfallow_prev"], np.nan, pool["summerfallow_implied"], sf_actual),
                  ("Cropland + summer fallow", pool["cropland_prev"] + pool["summerfallow_prev"], np.nan,
                   pool["allocated_total"] + pool["summerfallow_implied"], actual_total + sf_actual)]
        for item, a, u, f, c in totals:
            balance.append({"province": prov, "section": "total", "item": item, f"acres_{year - 1}": a,
                            f"unconstrained_{year}": u, f"allocated_{year}": f, f"cansim_{year}": c})
        for item, val in (("Summer fallow floor (25-yr rolling min)", pool["sf_floor"]),
                          ("Summer fallow floor year", pool["sf_floor_year"]),
                          ("Available fallow buffer", pool["available_fallow"]),
                          ("Land pool L = C + (SF - SF_min)", pool["land_pool"]),
                          ("Unconstrained basket S", pool["unconstrained_total"]),
                          ("Case (A: S <= L, B: scaled)", pool["case"]),
                          ("Scale factor", pool["scale_factor"])):
            balance.append({"province": prov, "section": "pool math", "item": item, f"allocated_{year}": val})

    fits_all = pd.concat(all_fits, ignore_index=True)
    check = check_against_vintage(fits_all, run_name, vintage)
    return {"forecast": pd.concat(forecasts, ignore_index=True), "balance": pd.DataFrame(balance),
            "pools": pd.DataFrame(pools), "check": check, "missing": missing_notes, "data": data}


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
def _wape(df: pd.DataFrame, col: str) -> float:
    return float((df[col] - df["acres_actual"]).abs().sum() / df["acres_actual"].sum())


def _k(v) -> str:
    return "—" if pd.isna(v) else f"{v / 1000:,.1f}"


def _pc(v) -> str:
    return "—" if pd.isna(v) else f"{v:+.1%}"


def report(res: dict, year: int, run_name: str, vintage: int) -> str:
    fc, pools = res["forecast"], res["pools"].set_index("province")
    applied = fc[fc["status"] == "APPLY_ELASTICITY"]
    prices = fc.dropna(subset=["price_source_j"])
    src = prices["price_source_j"].value_counts().to_dict()
    md = [f"# {year} acreage forecast vs CANSIM — {S.WINDOW_LABELS[run_name]}, parameters from the "
          f"{vintage}/{vintage + 1} calibration",
          "",
          f"Generated {date.today().isoformat()} by `src/run_forecast.py` · spec `model_crop_wallet_step1.md` §8–§9. "
          f"Parameters: {res['check']}.",
          ""]
    pdq_crops = [c for c, spec in S.CROPS.items() if spec["pdq"]]
    pdq_lines = prices[prices["crop"].isin(pdq_crops) & (prices["status"] == "APPLY_ELASTICITY")]
    fell_back = pdq_lines[pdq_lines["price_source_j"] != S.TIER_PDQ]
    tiers = ", ".join(f"{k} {v}" for k, v in src.items())
    if not fell_back.empty:
        md += [f"> **Price basis warning.** {len(fell_back)} forecast line(s) for PDQ crops have no {year} {run_name} "
               "prompt bid and use a fallback price: " + ", ".join(f"{r.province} {r.crop}" for r in fell_back.itertuples())
               + ". Their baseline R̄ is built from prompt bids, so the live deviation mixes price bases.", ""]
    md += [f"**Prices** ({year} {run_name}, crop lines by tier: {tiers}). CWRS, Durum (S ALTA / SW SASK only), "
           "Canola and Yellow Peas use PDQ prompt bids from the window. Barley, Oats, Lentils, Soybeans and Flax have no "
           "PDQ series and use the provincial guide price (BUDGET_FALLBACK) by design. MISSING_PRICE_DATA lines are "
           "regional exclusions held at 2025.", ""]
    md += ["## Accuracy summary", "",
           "Errors vs CANSIM for the crops whose elasticity was applied. The benchmark is the no-change forecast "
           "(A_T = A_T-1). MAPE = mean absolute % error (small crops weigh as much as Canola). WAPE = Σ|F − A| / ΣA "
           "(acreage-weighted, the better headline).", "",
           "| Province | Forecast lines | WAPE model | WAPE no-change | MAPE model | MAPE no-change | Total cropland error |",
           "|---|---|---|---|---|---|---|"]
    for prov in S.PROVINCES:
        p = fc[fc["province"] == prov]
        a = applied[(applied["province"] == prov) & applied["pct_error"].notna()]
        tot = pools.loc[prov]
        tot_err = _pct(tot["allocated_total"], tot["cropland_actual"])
        md.append(f"| {S.PROVINCES[prov]} | {len(a)} | {_wape(a, 'acres_allocated'):.1%} | {_wape(a, 'acres_prev'):.1%} | "
                  f"{a['pct_error'].abs().mean():.1%} | {a['naive_pct_error'].abs().mean():.1%} | {_pc(tot_err)} |")
    a_all = applied[applied["pct_error"].notna()]
    md += [f"| **All** | {len(a_all)} | {_wape(a_all, 'acres_allocated'):.1%} | {_wape(a_all, 'acres_prev'):.1%} | "
           f"{a_all['pct_error'].abs().mean():.1%} | {a_all['naive_pct_error'].abs().mean():.1%} | |", "",
           "## Land pool", "",
           "| Province | Cropland 2025 | Fallow 2025 | Fallow floor (year) | Land pool | Unconstrained S | Case | Scale | "
           f"Allocated cropland | Implied fallow {year} | CANSIM fallow {year} |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    bal = res["balance"]
    for prov in S.PROVINCES:
        t = pools.loc[prov]
        sf_act = bal[(bal["province"] == prov) & (bal["item"] == "Summer fallow")][f"cansim_{year}"].iloc[0]
        md.append(f"| {S.PROVINCES[prov]} | {_k(t['cropland_prev'])} | {_k(t['summerfallow_prev'])} | "
                  f"{_k(t['sf_floor'])} ({int(t['sf_floor_year'])}) | {_k(t['land_pool'])} | {_k(t['unconstrained_total'])} | "
                  f"{t['case']} | {t['scale_factor']:.4f} | {_k(t['allocated_total'])} | {_k(t['summerfallow_implied'])} | "
                  f"{_k(sf_act)} |")
    md += ["", "Acres in thousands. Case A = the unconstrained basket fits inside the pool, so nothing is scaled and "
           "fallow absorbs the change.", ""]

    md += ["## Variance by crop and province", "",
           "Acres in thousands. Variance = allocated forecast − CANSIM. Unconstrained = 2025 × exp(α₀ + β·x). "
           "— = not published.", ""]
    for prov in S.PROVINCES:
        p = fc[fc["province"] == prov]
        md += [f"### {S.PROVINCES[prov]} ({prov})", "",
               "| Line | Status | β / α₀ | x = ln(R/R̄) | Price source (crop / anchor) | 2025 | Unconstrained | Allocated | "
               f"CANSIM {year} | Variance | % error | No-change % error | Flags |",
               "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for _, r in p.iterrows():
            ba = "—" if pd.isna(r.get("beta")) else f"{r['beta']:.3f} / {r['alpha0']:+.3f}"
            x = "—" if pd.isna(r.get("x_dev")) else f"{r['x_dev']:+.3f}"
            ps = "—" if pd.isna(r.get("price_source_j")) else f"{r['price_source_j']} / {r['price_source_a']}"
            md.append(f"| {r['line']} | {r['status']} | {ba} | {x} | {ps} | {_k(r['acres_prev'])} | "
                      f"{_k(r['acres_unconstrained'])} | {_k(r['acres_allocated'])} | {_k(r['acres_actual'])} | "
                      f"{_k(r['variance'])} | {_pc(r['pct_error'])} | {_pc(r['naive_pct_error'])} | {r['flags'] or ''} |")
        t = pools.loc[prov]
        md += [f"| **Total cropland** | | | | | {_k(t['cropland_prev'])} | {_k(t['unconstrained_total'])} | "
               f"{_k(t['allocated_total'])} | {_k(t['cropland_actual'])} | "
               f"{_k(t['allocated_total'] - t['cropland_actual'])} | {_pc(_pct(t['allocated_total'], t['cropland_actual']))} | "
               f"{_pc(_pct(t['cropland_prev'], t['cropland_actual']))} | |", ""]

    notes = []
    for prov, mp, ma in res["missing"]:
        if mp or ma:
            notes.append(f"- {prov}: unpublished series counted as 0 — {year - 1}: {', '.join(mp) or 'none'}; "
                         f"{year}: {', '.join(ma) or 'none'}.")
    md += ["## Notes", "",
           "- **Basket**: StatCan 32-10-0359 seeded area. Wheat = 'Wheat, all including winter wheat remaining', split into "
           "CWRS, Durum and other wheat. All other principal field crops are included; tame hay and corn for silage are "
           "excluded. Lines that aren't forecast (other wheat, corn, other field crops, exclusions) are held at "
           f"{year - 1}.",
           f"- **CANSIM {year}** = the latest {year} seeded-area estimate in the local 32-10-0359 extract.",
           "- **Yields** use the 21-year trend evaluated at the forecast year, and **costs** come from the 2026 workbook.",
           *notes, ""]
    return "\n".join(md)


def main() -> int:
    parser = argparse.ArgumentParser(description="One-step-forward acreage forecast and CANSIM evaluation.")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--run", default="RUN3", choices=S.WINDOWS)
    parser.add_argument("--vintage", type=int, default=2025)
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    res = run(args.year, args.run, args.vintage)
    tag = f"{args.year}_{args.run.lower().replace('run', 'run')}"
    fc_path = S.OUTPUT_DIR / f"forecast_{tag}.csv"
    bal_path = S.OUTPUT_DIR / f"land_pool_balance_sheet_{args.year}.csv"
    md_path = S.REPORTS_DIR / f"{args.year}_forecast_vs_cansim.md"
    S.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    cols = ["province", "line", "crop", "tier", "anchor", "status", "flags", "fit_status", "beta_flag", "alpha0",
            "beta", "fit_n", "price_j", "price_source_j", "price_a", "price_source_a", "exp_yield_j",
            "variable_cost_j", "rpa_j", "rpa_a", "ratio_live", "ratio_baseline", "baseline_n", "x_dev",
            "growth_factor", "acres_prev", "acres_unconstrained", "acres_allocated", "acres_actual", "variance",
            "pct_error", "naive_pct_error"]
    res["forecast"][cols].rename(columns={"acres_prev": f"acres_{args.year - 1}",
                                          "acres_actual": f"cansim_{args.year}"}).to_csv(fc_path, index=False)
    res["balance"].to_csv(bal_path, index=False)
    md_path.write_text(report(res, args.year, args.run, args.vintage), encoding="utf-8")
    for p in (fc_path, bal_path, md_path):
        print(f"Wrote {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
