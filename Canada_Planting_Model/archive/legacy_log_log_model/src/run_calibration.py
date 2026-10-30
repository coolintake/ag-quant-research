"""Re-estimate the run-specific acreage elasticities: loop Province (MB, SK, AB) x Window (Run 1-3).

    python src/run_calibration.py

Per cell: StatCan acres and 21-year trend yields, budget variable costs and resolved prices
(PDQ prompt -> template -> guide -> NULL; Durum: S ALTA / SW SASK) for the province; RPA, tiered
pair ratios, 5-year baselines; log-log OLS with bounded beta and flags (spec 6-7). Each run's
own-sample fit is the parameter set its release uses. Run 1-3 are then refit on their common
years for the diagnostic window ranking (spec 7.3), which never changes the parameters.

Outputs (output/reports/)
    calibration_summary.md / .csv   run-specific parameters, diagnostic ranking, data notes
    fits_by_vintage.csv             fits on t <= V for each vintage V (elasticity workbook source)
    yield_trend_components.csv      actual / trend / SMA yields 1998-2026 (yield workbook source)
    parameters_by_run.csv           alpha0 / beta / flags per province x pair x run (release inputs)
    fits_own_sample.csv             every fit, incl. OLS audit columns
    fits_common_sample.csv          Run 1-3 on shared years (diagnostic)
    window_selection.csv            diagnostic winner per province x pair
    rpa_ratios.csv                  ratios, baselines, deviations, NULL reasons
    panel_inputs.csv                acres, yields, costs, prices, RPA per cell
    yields_expected.csv             trend / fallback yields with method and flags
    prices_resolved.csv, prices_prompt_windows.csv
    costs.csv, cost_check_2026.csv, workbook_validation.csv
Charts: output/charts/r2_by_window.png, output/charts/fit_scatter.png
Workbooks: output/rpa_elasticity_matrices.xlsx, output/yield_trend_diagnostics.xlsx
"""

from __future__ import annotations

import logging
from datetime import date

import numpy as np
import pandas as pd

import charts
import data_loader
import excel_exports
import regression_engine as reg
import settings as S

log = logging.getLogger("calibration")


def _f(v, nd=3) -> str:
    return "—" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:.{nd}f}"


def _p(v) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    return "<0.001" if v < 0.001 else f"{v:.3f}"


def _pair_label(pair: str) -> str:
    crop, anchor = pair.split("/")
    tier = S.TIERS[(crop, anchor)]
    return f"T{tier} · {S.CROPS[crop]['label']} / {S.CROPS[anchor]['label']}"


def _stats(r: pd.Series) -> dict:
    """Display values for one fit. Inverted fits show the bounded prior, never the negative beta."""
    inverted = r["flag"] == "INVALID_INVERTED_SLOPE"
    return {
        "beta": _f(r["beta_bounded"]) + (" (prior)" if inverted else ""),
        "alpha": _f(r["alpha0_bounded"]),
        "alpha_se": "n/a" if inverted else _f(r["alpha0_se"]),
        "beta_se": "n/a" if inverted else _f(r["beta_se"]),
        "p": "n/a" if inverted else _p(r["beta_p"]),
        "r2": _f(r["r2_bounded"]),
        "flag": r["flag"] + ("; LOW_N" if r["status"] == "LOW_N" else ""),
    }


# --------------------------------------------------------------------------- #
# Report sections
# --------------------------------------------------------------------------- #
def parameter_tables(own: pd.DataFrame) -> tuple[list[str], pd.DataFrame]:
    """Run-specific parameter matrix (own-sample fits): what each release uses."""
    rows, lines = [], []
    for prov in S.PROVINCES:
        lines += [f"### {S.PROVINCES[prov]} ({prov})", "",
                  "| Pair | " + " | ".join(f"{r} β / α₀ / R² / N" for r in S.WINDOWS) + " | Flags |",
                  "|---|" + "---|" * len(S.WINDOWS) + "---|"]
        for crop, anchor in S.PAIRS:
            pair = f"{crop}/{anchor}"
            cells, flags = [], []
            for run in S.WINDOWS:
                r = own[(own["province"] == prov) & (own["pair"] == pair) & (own["run"] == run)].iloc[0]
                rec = {"province": prov, "tier": S.TIERS[(crop, anchor)], "pair": pair, "run": run,
                       "status": r["status"], "n": int(r["n"]), "years": r["years"],
                       "forecast_action": reg.forecast_action(r["status"])}
                if r["status"] == S.EXCLUDED_STATUS:
                    cells.append("N/A")
                    if run == S.WINDOWS[0]:
                        flags.append(S.EXCLUDED_STATUS)
                elif r["status"] not in reg.FIT_OK:
                    cells.append(f"— (N={int(r['n'])})")
                    flags.append(f"{run}: UNCALIBRATED")
                else:
                    d = _stats(r)
                    cells.append(f"{d['beta']} / {d['alpha']} / {d['r2']} / {int(r['n'])}")
                    if r["flag"] != "INTERIOR" or r["status"] == "LOW_N":
                        flags.append(f"{run}: {d['flag'].replace('INTERIOR; ', '')}")
                    rec.update(alpha0=r["alpha0_bounded"], beta=r["beta_bounded"], r2=r["r2_bounded"],
                               flag=r["flag"], beta_se=r["beta_se"], beta_p=r["beta_p"])
                rows.append(rec)
            lines.append(f"| {_pair_label(pair)} | " + " | ".join(cells) + f" | {'; '.join(flags) or '—'} |")
        lines.append("")
    return lines, pd.DataFrame(rows)


def winners_table(selection: pd.DataFrame, own: pd.DataFrame, common: pd.DataFrame) -> tuple[list[str], pd.DataFrame]:
    lines = ["| Province | Crop / Anchor pair | Winning window | β (bounded) | α₀ | R² | p(β) | SE(β) | SE(α₀) | N | Flags |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    rows = []
    for prov in S.PROVINCES:
        for _, s in selection[selection["province"] == prov].iterrows():
            if not s["winning_run"]:
                lines.append(f"| {prov} | {_pair_label(s['pair'])} | none | — | — | — | — | — | — | — | {s['basis']} |")
                rows.append({"province": prov, "pair": s["pair"], "winning_run": None, "flags": s["basis"]})
                continue
            src = common if s["basis"] == "common" else own
            r = src[(src["province"] == prov) & (src["pair"] == s["pair"]) & (src["run"] == s["winning_run"])].iloc[0]
            d = _stats(r)
            flags = d["flag"] + ("; own-sample basis" if s["basis"] == "own" else "")
            lines.append(f"| {prov} | {_pair_label(s['pair'])} | **{S.WINDOW_LABELS[s['winning_run']]}** | {d['beta']} | "
                         f"{d['alpha']} | {d['r2']} | {d['p']} | {d['beta_se']} | {d['alpha_se']} | {int(r['n'])} | {flags} |")
            rows.append({"province": prov, "pair": s["pair"], "winning_run": s["winning_run"], "basis": s["basis"],
                         "years": r["years"], "n": r["n"], "beta_bounded": r["beta_bounded"],
                         "alpha0_bounded": r["alpha0_bounded"], "r2": r["r2_bounded"], "beta_p": r["beta_p"],
                         "beta_se": r["beta_se"], "alpha0_se": r["alpha0_se"], "flags": flags})
    return lines, pd.DataFrame(rows)


def comparison_table(prov: str, selection: pd.DataFrame, own: pd.DataFrame, common: pd.DataFrame) -> list[str]:
    lines = [f"### {S.PROVINCES[prov]} ({prov})", "",
             "| Pair | Run | Sample | N | β (bounded) | α₀ | R² | p(β) | SE(β) | Flag | Winner |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    sel = selection[selection["province"] == prov].set_index("pair")
    for crop, anchor in S.PAIRS:
        pair = f"{crop}/{anchor}"
        basis = sel.loc[pair, "basis"]
        src = own if basis != "common" else common
        for run in S.WINDOWS:
            label = _pair_label(pair) if run == "RUN1" else ""
            hit = src[(src["province"] == prov) & (src["pair"] == pair) & (src["run"] == run)]
            if hit.empty:
                hit = own[(own["province"] == prov) & (own["pair"] == pair) & (own["run"] == run)]
            r = hit.iloc[0]
            sample = "common" if basis == "common" else "own"
            if r["status"] not in reg.FIT_OK:
                lines.append(f"| {label} | {run} | {sample} | {int(r['n'])} | — | — | — | — | — | {r['status']} | |")
                continue
            d = _stats(r)
            win = "**✓**" if sel.loc[pair, "winning_run"] == run else ""
            lines.append(f"| {label} | {run} | {sample} | {int(r['n'])} | {d['beta']} | {d['alpha']} | {d['r2']} | "
                         f"{d['p']} | {d['beta_se']} | {d['flag']} | {win} |")
    lines.append("")
    return lines


def province_table(prov_win: dict) -> list[str]:
    lines = ["| Province | Pair wins R1 / R2 / R3 | Mean valid R² R1 / R2 / R3 | Valid fits R1 / R2 / R3 | Province winning window |",
             "|---|---|---|---|---|"]
    for prov, w in prov_win.items():
        lines.append(
            f"| {S.PROVINCES[prov]} | {' / '.join(str(w['wins'][r]) for r in S.WINDOWS)} | "
            f"{' / '.join(_f(w['mean_valid_r2'][r]) for r in S.WINDOWS)} | "
            f"{' / '.join(str(w['n_valid'][r]) for r in S.WINDOWS)} | "
            f"**{S.WINDOW_LABELS[w['winning_run']] if w['winning_run'] else 'none'}** |")
    return lines


def null_table(ratios: pd.DataFrame) -> list[str]:
    w = ratios[ratios["year"] > S.YEARS[0]]
    nulls = w[w["ratio_null_reason"].notna()]
    agg = (nulls.groupby(["province", "pair", "ratio_null_reason"])
                .apply(lambda g: ", ".join(f"{r}:{y}" for r, y in zip(g["run"], g["year"])), include_groups=False)
                .reset_index(name="cells"))
    lines = ["| Province | Pair | Reason | Run:year |", "|---|---|---|---|"]
    lines += [f"| {r['province']} | {r['pair']} | {r['ratio_null_reason']} | {r['cells']} |" for _, r in agg.iterrows()]
    return lines


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    S.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    S.CHARTS_DIR.mkdir(parents=True, exist_ok=True)

    data = data_loader.build_panel()
    panel = data["panel"]

    ratio_frames, own_frames, common_frames, selections, prov_win = [], [], [], [], {}
    for prov in S.PROVINCES:                                   # MB, SK, AB
        prov_ratios, prov_own = [], []
        for run in S.WINDOWS:                                  # Run 1, 2, 3
            cell = panel[(panel["province"] == prov) & (panel["run"] == run)]   # steps 1-4: province filters + RPA
            ratios = reg.build_cell_ratios(cell)                                # steps 5-6: ratios + baselines
            ratios.insert(0, "province", prov)
            ratios.insert(1, "run", run)
            prov_ratios.append(ratios)
            prov_own.append(reg.fit_cell(ratios, prov, run))                    # step 7: OLS, bounded beta
        ratios_p = pd.concat(prov_ratios, ignore_index=True)
        own_p = pd.concat(prov_own, ignore_index=True)
        common_p = reg.fit_common(ratios_p, prov)
        sel_p = reg.select_windows(common_p, own_p, prov)
        prov_win[prov] = reg.province_window(sel_p, common_p, own_p)
        ratio_frames.append(ratios_p); own_frames.append(own_p)
        common_frames.append(common_p); selections.append(sel_p)

    ratios = pd.concat(ratio_frames, ignore_index=True)
    own = pd.concat(own_frames, ignore_index=True)
    common = pd.concat(common_frames, ignore_index=True)
    selection = pd.concat(selections, ignore_index=True)

    winners_md, winners = winners_table(selection, own, common)
    params_md, params = parameter_tables(own)
    vintages = reg.fit_vintages(ratios)
    vintage_avg = reg.average_vintages(vintages)
    excel_exports.write_elasticity_matrices(vintages, vintage_avg)
    components = data_loader.yield_components(
        data_loader.load_statcan(first_year=S.YIELD_DIAGNOSTIC_YEARS[0] - S.YIELD_TREND_YEARS - 1),
        S.YIELD_DIAGNOSTIC_YEARS)
    excel_exports.write_yield_diagnostics(components)
    out = S.REPORTS_DIR
    winners.to_csv(out / "calibration_summary.csv", index=False)
    own.to_csv(out / "fits_own_sample.csv", index=False)
    common.to_csv(out / "fits_common_sample.csv", index=False)
    selection.to_csv(out / "window_selection.csv", index=False)
    ratios.to_csv(out / "rpa_ratios.csv", index=False)
    panel.to_csv(out / "panel_inputs.csv", index=False)
    params.to_csv(out / "parameters_by_run.csv", index=False)
    vintages.to_csv(out / "fits_by_vintage.csv", index=False)
    vintage_avg.to_csv(out / "fits_vintage_average.csv", index=False)
    components.to_csv(out / "yield_trend_components.csv", index=False)
    data["yields"].to_csv(out / "yields_expected.csv", index=False)
    data["prices"].to_csv(out / "prices_resolved.csv", index=False)
    data["prompt"].to_csv(out / "prices_prompt_windows.csv", index=False)
    data["costs"].to_csv(out / "costs.csv", index=False)
    data["cost_check"].to_csv(out / "cost_check_2026.csv", index=False)
    data["workbook_validation"].to_csv(out / "workbook_validation.csv", index=False)

    charts.r2_by_window(pd.concat([own, common], ignore_index=True), selection, S.CHARTS_DIR / "r2_by_window.png")
    charts.fit_scatter(ratios, own, S.CHARTS_DIR / "fit_scatter.png")

    y = data["yields"]
    fb = y[y["yield_flag"].notna()].groupby(["province", "crop"])["year"].agg(lambda v: f"{v.min()}–{v.max()}")
    yield_fallbacks = ", ".join(f"{p} {c} ({yrs})" for (p, c), yrs in fb.items()) or "none"
    tier_counts = ", ".join(f"{k} {v}" for k, v in panel["price_source"].value_counts().items())
    val = data["workbook_validation"]
    chk = data["cost_check"]
    fert_gap = chk.loc[chk["fertilizer_diff"].abs() > 0.5, ["province", "crop", "fertilizer_diff"]]
    md = [
        "# Acreage elasticity re-estimation, 2019–2025 — run-specific parameters by province",
        "",
        f"Generated {date.today().isoformat()} by `src/run_calibration.py` · spec `model_crop_wallet_step1.md` §5–§7.",
        "",
        "ln(Acres_t / Acres_t-1) = α₀,j,p,w + β_j,p,w · ln(R_t / R̄_t-1). R = RPA_crop / RPA_anchor; RPA = trend yield × "
        "price − (seed + fertilizer + chemicals); R̄ = mean R over t-5 … t-1 in the same window. Tiers: T0 CWRS on the "
        "inverted CWRS/Canola ratio, T1 vs CWRS, T2 vs Durum or Canola. β is the bounded estimate on [0.10, 2.00]. "
        "**INVALID_INVERTED_SLOPE** = OLS β ≤ 0: β shows the 0.10 floor with α₀ re-fitted (marked “prior”), and the "
        "negative value is never reported. **AT_LOWER_BOUND** = 0 < OLS β < 0.10. **LOW_N** = N < 5. "
        "**UNCALIBRATED** = N < 3. **N/A** = EXCLUDED_REGIONAL_CROP (no regression). Both hold acres at T-1. "
        "SE and p refer to the OLS slope.",
        "",
        "## Run-specific parameters (each release uses its own run)",
        "",
        "Cell = β / α₀ / R² / N from each run's own-sample fit.",
        "",
        *params_md,
        "## Diagnostic window ranking (does not select parameters)",
        "",
        "Runs compared on the years all three share. Winner = highest R² among fits with OLS β > 0.",
        "",
        *winners_md,
        "",
        *province_table(prov_win),
        "",
        "## Run 1 / Run 2 / Run 3 comparison (ranking basis)",
        "",
    ]
    for prov in S.PROVINCES:
        md += comparison_table(prov, selection, own, common)
    md += [
        "## Data notes",
        "",
        f"- **Variable costs**: 2026 seed, fertilizer and chemical come from `RPA_Master_Calculator.xlsx` "
        f"(`RPA_Summary_<prov>`), the only year the workbook budgets per crop. 2019–2025 come from RPA_Calculator's "
        f"normalized history tables (`output/<prov>/mvs`). Those cost lines reproduce all {val['abs_diff'].notna().sum()} "
        f"published 2019–2025 values on the workbook's `Historical_Ratios` sheet (max |diff| {val['abs_diff'].max():.1e}). "
        f"No OCR output or raw guide tables are read. Fuel is excluded per [DCASH].",
        "- **2026 cross-check, history table vs workbook**: seed and chemical match exactly. Fertilizer differs where "
        "the workbook recomputes it with its N-credit rule: "
        + (", ".join(f"{r.province} {r.crop} {r.fertilizer_diff:+.2f} $/ac" for r in fert_gap.itertuples()) or "none > $0.50/ac")
        + ". The workbook value is used.",
        f"- **Yields**: StatCan 32-10-0359 provincial actuals. Each year T uses an OLS linear trend on T-21 … T-1 "
        f"evaluated at T (at least {S.YIELD_TREND_MIN_N} points), so no year's own yield is used. Otherwise the T-5 … T-1 mean is used, "
        f"flagged INSUFFICIENT_YIELD_HISTORY: {yield_fallbacks}. Units are bu/ac; Lentils stay in lb/ac.",
        f"- **Prices** (spec §4): PDQ prompt bids (window start delivery month, else the earliest later month up to JUL "
        f"of T). CWRS, Canola and Yellow Peas use every PDQ zone; Durum uses S ALTA (AB) and SW SASK (SK) only. "
        f"Cells without a prompt bid use the template, then the year's provincial guide price, then NULL. Tier counts over "
        f"the calibration cells: {tier_counts}. Barley, Oats, Lentils, Soybeans and Flax are guide-priced (the same in "
        f"all runs), so those pairs differ by window only through the anchor's prompt price.",
        "- **Regional exclusions** (EXCLUDED_REGIONAL_CROP, acres held at T-1): "
        + ", ".join(f"{p} {c}" for p, c in sorted(S.REGIONAL_EXCLUSIONS)) + ". CORN is not a modelled crop.",
        f"- **Sample**: planting years {S.YEARS[0]}–{S.YEARS[-1]}. {S.YEARS[0]} supplies t-1 and the first baseline, so N ≤ "
        f"{len(S.YEARS) - 1}.",
        "- **Barley** uses the feed barley budget (MB publishes no malt budget). StatCan acres are total barley.",
        "",
        "## Pairs not estimable",
        "",
        *[f"- **{k}**: {v}" for k, v in S.NOT_ESTIMABLE.items()],
        "",
        "## NULL observations",
        "",
        *null_table(ratios),
        "",
    ]
    (out / "calibration_summary.md").write_text("\n".join(md), encoding="utf-8")
    print(f"Wrote {out / 'calibration_summary.md'} and charts in {S.CHARTS_DIR}")
    print(f"Wrote {S.ELASTICITY_WORKBOOK}")
    print(f"Wrote {S.YIELD_WORKBOOK}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
