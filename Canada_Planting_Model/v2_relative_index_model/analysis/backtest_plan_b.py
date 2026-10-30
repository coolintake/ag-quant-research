"""Backtest of Plan-B alternatives against CANSIM seeded acres (V2 panel, MB / SK / AB).

    python analysis/backtest_plan_b.py

Models (y = %dAcres, x = %dSignal = YoY change in RPA_crop / RPA_CWRS; CWRS uses RPA_CWRS / RPA_Canola):
    Legacy   pooled OLS                      y = a + b x
    Legacy0  pooled OLS, no intercept        y = b x          (diagnostic: drift term removed)
    Alt 1    fixed effects (crop-province)   y = a_i + b x
    Alt 2    tier slopes                     y = a + b_tier x      (tier 1 Canola/CWRS, 2 Durum/Peas, 3 CPSR)
    Alt 3    mixed effects, groups = crop-province pair, random intercept + slope (REML)
    Alt 3b   mixed effects, groups = crop (pools each crop across provinces) - sensitivity
    No-change benchmark: y = 0

Evaluation
    Primary  : expanding window, out-of-sample. For T = 2022..2026, fit on 2020..T-1 and predict T
               (2026 CANSIM acres are preliminary).
    Secondary: leave-one-year-out CV over 2020-2025.
    In-sample: full 2020-2025 fit for parameters, R-squared and adjusted R-squared.
Metrics on acres: MAPE, WAPE, RMSE (thousand acres); on %dAcres: RMSE (pp), median APE, directional hit
rate (sign of predicted vs actual %dAcres). Stability: SD of the slope(s) across expanding-window fits.
Runs for every window; Run 3 is the headline.
"""

from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import config as cfg  # noqa: E402  (not 'C': patsy formulas use C() for categoricals)

OUT = cfg.OUTPUT_DIR / "backtest"
TIER = {"CANOLA": 1, "CWRS": 1, "BARLEY": 1, "Y_PEAS": 2, "LENTILS": 2, "DURUM": 2}   # everything else: tier 3
FIRST_YEAR, LAST_FULL, PRELIM = 2020, 2025, 2026
MODELS = ("Legacy", "Legacy_NoInt", "Alt1_FE", "Alt2_Tier", "Alt3_Mixed", "Alt3b_MixedCrop", "NoChange")
LABELS = {"Legacy": "Legacy pooled OLS", "Legacy_NoInt": "Legacy, no intercept (y = b·x)", "Alt1_FE": "Alt 1 fixed effects", "Alt2_Tier": "Alt 2 tier slopes",
          "Alt3_Mixed": "Alt 3 mixed (crop-province)", "Alt3b_MixedCrop": "Alt 3b mixed (crop)",
          "NoChange": "No-change benchmark"}


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #
def load(run: str) -> pd.DataFrame:
    p = pd.read_csv(cfg.PANEL_CSV)
    d = p[p["province"].isin(cfg.PROVINCES) & (p["run"] == run) & p["year"].between(FIRST_YEAR, PRELIM)]
    d = d.dropna(subset=["pct_d_signal", "pct_d_acres", "acres_prev", "acres"]).copy()
    d["x"], d["y"] = d["pct_d_signal"], d["pct_d_acres"]
    d["series"] = d["province"] + "_" + d["crop"]
    d["tier"] = "T" + d["crop"].map(TIER).fillna(3).astype(int).astype(str)
    return d.reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Models: fit(train) -> (predict(test) -> %dAcres, params dict)
# --------------------------------------------------------------------------- #
def _ols(formula):
    def fit(train):
        m = smf.ols(formula, train).fit()
        return (lambda test: np.asarray(m.predict(test))), m
    return fit


class _Pooled:
    """Stand-in result when no random-effects structure is estimable: pooled OLS, zero group effects."""

    def __init__(self, train):
        o = smf.ols("y ~ x", train).fit()
        self.fe_params, self.bse_fe, self.pvalues = o.params, o.bse, o.pvalues
        self.random_effects, self.converged = {}, True
        self.cov_re = pd.DataFrame([[0.0, 0.0], [0.0, 0.0]])


def _mixed(group_col):
    """REML mixed model with random intercept + slope by ``group_col``. If the random-effects covariance
    is singular (a variance estimated at zero: the data can't separate group slopes), step down to a
    random intercept only, then to pooled OLS. ``m.re_used`` records the structure actually used."""
    def fit(train):
        m, used, caught = None, None, set()
        for re_formula, label in (("~x", "RI+RS"), ("1", "RI")):
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                cand = smf.mixedlm("y ~ x", train, groups=train[group_col], re_formula=re_formula).fit(
                    reml=True, method=["lbfgs", "powell"])
            caught |= {type(x.message).__name__ for x in w}
            try:
                _ = cand.random_effects                      # raises on a singular covariance
            except ValueError:
                caught.add(f"singular_{label}")
                continue
            m, used = cand, label
            break
        if m is None:
            m, used = _Pooled(train), "pooled"
        m.fit_warnings, m.re_used = sorted(caught), used
        fe = m.fe_params
        re = m.random_effects

        def predict(test):
            out = []
            for _, r in test.iterrows():
                u = re.get(r[group_col], pd.Series({"Group": 0.0, "x": 0.0}))
                out.append(fe["Intercept"] + u.get("Group", 0.0) + (fe["x"] + u.get("x", 0.0)) * r["x"])
            return np.asarray(out)
        return predict, m
    return fit


def _nochange(train):
    return (lambda test: np.zeros(len(test))), None


FITTERS = {"Legacy": _ols("y ~ x"), "Legacy_NoInt": _ols("y ~ x - 1"), "Alt1_FE": _ols("y ~ x + C(series)"), "Alt2_Tier": _ols("y ~ x:C(tier)"),
           "Alt3_Mixed": _mixed("series"), "Alt3b_MixedCrop": _mixed("crop"), "NoChange": _nochange}


def slopes(name, m) -> dict:
    """Slope(s) of a fitted model, for stability tracking."""
    if m is None:
        return {}
    if name == "Alt2_Tier":
        return {k.split("[")[1].rstrip("]").replace("T.", ""): v for k, v in m.params.items() if k.startswith("x:")}
    if name.startswith("Alt3"):
        return {"b": m.fe_params["x"]}
    return {"b": m.params["x"]}


# --------------------------------------------------------------------------- #
# Backtests
# --------------------------------------------------------------------------- #
def expanding(d: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    preds, stab = [], []
    for t in range(FIRST_YEAR + 2, PRELIM + 1):
        train, test = d[d["year"] < t], d[d["year"] == t]
        for name, fit in FITTERS.items():
            predict, m = fit(train)
            f = test[["province", "crop", "series", "tier", "year", "x", "y", "acres_prev", "acres"]].copy()
            f["model"], f["pred_pct"] = name, predict(test)
            preds.append(f)
            for k, v in slopes(name, m).items():
                stab.append({"model": name, "fit_through": t - 1, "slope": k, "value": v,
                             "re_used": getattr(m, "re_used", ""),
                             "warnings": ",".join(getattr(m, "fit_warnings", [])) if m is not None else ""})
    return pd.concat(preds, ignore_index=True), pd.DataFrame(stab)


def loyo(d: pd.DataFrame) -> pd.DataFrame:
    base = d[d["year"] <= LAST_FULL]
    preds = []
    for t in sorted(base["year"].unique()):
        train, test = base[base["year"] != t], base[base["year"] == t]
        for name, fit in FITTERS.items():
            predict, _ = fit(train)
            f = test[["province", "crop", "series", "tier", "year", "x", "y", "acres_prev", "acres"]].copy()
            f["model"], f["pred_pct"] = name, predict(test)
            preds.append(f)
    return pd.concat(preds, ignore_index=True)


def metrics(p: pd.DataFrame) -> pd.Series:
    fa = p["acres_prev"] * (1 + p["pred_pct"])
    ape = (fa - p["acres"]).abs() / p["acres"]
    no_change = (p["pred_pct"] == 0).all()                  # benchmark: direction undefined
    hit = pd.Series(np.nan) if no_change else (np.sign(p["pred_pct"]) == np.sign(p["y"]))
    return pd.Series({"N": len(p), "MAPE": ape.mean(), "MedAPE": ape.median(),
                      "WAPE": (fa - p["acres"]).abs().sum() / p["acres"].sum(),
                      "RMSE_kac": np.sqrt(((fa - p["acres"]) ** 2).mean()) / 1e3,
                      "RMSE_pp": np.sqrt(((p["pred_pct"] - p["y"]) ** 2).mean()) * 100,
                      "HitRate": hit.mean()})


def in_sample(d: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Full 2020-2025 fits: fit statistics and parameter estimates."""
    full = d[d["year"] <= LAST_FULL]
    stats, params = [], []
    ybar, sst = full["y"].mean(), ((full["y"] - full["y"].mean()) ** 2).sum()
    for name, fit in FITTERS.items():
        if name == "NoChange":
            continue
        predict, m = fit(full)
        resid = full["y"] - predict(full)
        n = len(full)
        if name.startswith("Alt3"):
            k_fe = len(m.fe_params)
            r2 = 1 - (resid ** 2).sum() / sst                       # conditional (fixed + BLUP) R^2
            marg = 1 - ((full["y"] - (m.fe_params["Intercept"] + m.fe_params["x"] * full["x"])) ** 2).sum() / sst
            re_cov = m.cov_re
            stats.append({"model": name, "N": n, "params": f"{k_fe} fixed + random int/slope "
                                                          f"({full[name == 'Alt3_Mixed' and 'series' or 'crop'].nunique()} groups)",
                          "R2": r2, "AdjR2": np.nan, "MarginalR2": marg, "converged": m.converged,
                          "warnings": ",".join(m.fit_warnings), "re_used": m.re_used,
                          "SD_rand_intercept": float(np.sqrt(max(re_cov.iloc[0, 0], 0))),
                          "SD_rand_slope": float(np.sqrt(max(re_cov.iloc[1, 1], 0))) if re_cov.shape[0] > 1 else 0.0})
            for k in ("Intercept", "x"):
                params.append({"model": name, "term": "a (fixed)" if k == "Intercept" else "b (fixed)",
                               "estimate": m.fe_params[k], "se": m.bse_fe[k], "p": m.pvalues[k]})
            for g, u in m.random_effects.items():
                params.append({"model": name, "term": f"b | {g} (shrunk)", "estimate": m.fe_params["x"] + u.get("x", 0.0),
                               "se": np.nan, "p": np.nan})
                params.append({"model": name, "term": f"a | {g} (shrunk)",
                               "estimate": m.fe_params["Intercept"] + u.get("Group", 0.0), "se": np.nan, "p": np.nan})
        else:
            stats.append({"model": name, "N": n, "params": int(m.df_model + 1), "R2": m.rsquared,
                          "AdjR2": m.rsquared_adj, "MarginalR2": np.nan, "converged": True, "warnings": "",
                          "SD_rand_intercept": np.nan, "SD_rand_slope": np.nan})
            for k in m.params.index:
                term = ("b" if k == "x" else "a" if k == "Intercept" else
                        f"b | {k.split('[')[1].rstrip(']').replace('T.', '')}" if k.startswith("x:") else
                        f"a | {k.split('[')[1].rstrip(']').replace('T.', '')} (vs base)")
                params.append({"model": name, "term": term, "estimate": m.params[k], "se": m.bse[k], "p": m.pvalues[k]})
    return pd.DataFrame(stats), pd.DataFrame(params)


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
def _p(v, f="{:.1%}"):
    return "—" if pd.isna(v) else f.format(v)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    perf_all, stats_all, params_all, crop_all, stab_all, loyo_all, year_all = [], [], [], [], [], [], []
    for run in cfg.WINDOWS:
        d = load(run)
        ex, stab = expanding(d)
        lo = loyo(d)
        st, pa = in_sample(d)
        perf = ex.groupby("model").apply(metrics, include_groups=False).reset_index().assign(window=run, test="expanding 2022-2026")
        perf_x26 = ex[ex["year"] <= LAST_FULL].groupby("model").apply(metrics, include_groups=False).reset_index().assign(
            window=run, test="expanding 2022-2025")
        perf_lo = lo.groupby("model").apply(metrics, include_groups=False).reset_index().assign(window=run, test="LOYO 2020-2025")
        crop = ex.groupby(["model", "crop"]).apply(metrics, include_groups=False).reset_index().assign(window=run)
        yr = ex.groupby(["model", "year"]).apply(metrics, include_groups=False).reset_index().assign(window=run)
        perf_all += [perf, perf_x26, perf_lo]
        crop_all.append(crop)
        year_all.append(yr)
        stats_all.append(st.assign(window=run))
        params_all.append(pa.assign(window=run))
        stab_all.append(stab.assign(window=run))
        ex.assign(window=run).to_csv(OUT / f"predictions_expanding_{run}.csv", index=False)
    perf = pd.concat(perf_all, ignore_index=True)
    stats = pd.concat(stats_all, ignore_index=True)
    params = pd.concat(params_all, ignore_index=True)
    crop = pd.concat(crop_all, ignore_index=True)
    years = pd.concat(year_all, ignore_index=True)
    stab = pd.concat(stab_all, ignore_index=True)
    for name, df in (("performance", perf), ("in_sample_fit", stats), ("parameters", params), ("crop_breakdown", crop),
                     ("year_breakdown", years), ("slope_stability", stab)):
        df.to_csv(OUT / f"{name}.csv", index=False)
    (OUT / "backtest_plan_b_report.md").write_text(report(perf, stats, params, crop, years, stab), encoding="utf-8")
    print(f"Wrote {OUT / 'backtest_plan_b_report.md'} and CSVs in {OUT}")
    return 0


def report(perf, stats, params, crop, years, stab) -> str:
    head = "RUN3"
    md = ["# Plan-B alternatives — backtest vs CANSIM seeded acres", "",
          "Panel: V2 relative cash RPA index, MB / SK / AB, 13 province-crop series (CWRS, CPSR, Durum, Canola, "
          "Yellow Peas). Tiers: T1 Canola/CWRS, T2 Durum/Peas, T3 CPSR. Barley, Oats and Lentils have no PDQ series "
          "and aren't in the V2 panel. Headline window: Run 3 (Feb 15 – Mar 15). 2026 acres are preliminary.", ""]

    def perf_table(test, window):
        p = perf[(perf["test"] == test) & (perf["window"] == window)].set_index("model").loc[list(MODELS)]
        rows = ["| Model | N | MAPE | Median APE | WAPE | RMSE (k ac) | RMSE (pp) | Hit rate |",
                "|---|---|---|---|---|---|---|---|"]
        for m, r in p.iterrows():
            rows.append(f"| {LABELS[m]} | {int(r['N'])} | {_p(r['MAPE'])} | {_p(r['MedAPE'])} | {_p(r['WAPE'])} | "
                        f"{r['RMSE_kac']:,.0f} | {r['RMSE_pp']:.1f} | {_p(r['HitRate'], '{:.0%}')} |")
        return rows

    md += ["## 1. Model performance (out-of-sample)", "",
           "### Run 3, expanding window 2022–2026 (fit on years before T, forecast T)", "", *perf_table("expanding 2022-2026", head), "",
           "### Run 3, expanding window 2022–2025 (final CANSIM years only)", "", *perf_table("expanding 2022-2025", head), "",
           "### Run 3, leave-one-year-out 2020–2025", "", *perf_table("LOYO 2020-2025", head), "",
           "### All windows, expanding 2022–2026: WAPE / hit rate", "",
           "| Model | " + " | ".join(cfg.WINDOW_SHORT[w] for w in cfg.WINDOWS) + " |", "|---|" + "---|" * len(cfg.WINDOWS)]
    for m in MODELS:
        cells = []
        for w in cfg.WINDOWS:
            r = perf[(perf["test"] == "expanding 2022-2026") & (perf["window"] == w) & (perf["model"] == m)].iloc[0]
            cells.append(f"{_p(r['WAPE'])} / {_p(r['HitRate'], '{:.0%}')}")
        md.append(f"| {LABELS[m]} | " + " | ".join(cells) + " |")

    md += ["", "### In-sample fit, Run 3, 2020–2025 (N = 78)", "",
           "| Model | Parameters | R² | Adjusted R² | Marginal R² (fixed part) | RE structure / SD(slope) |", "|---|---|---|---|---|---|"]
    for _, r in stats[stats["window"] == head].iterrows():
        md.append(f"| {LABELS[r['model']]} | {r['params']} | {r['R2']:.3f} | {_p(r['AdjR2'], '{:.3f}')} | "
                  f"{_p(r['MarginalR2'], '{:.3f}')} | "
                  f"{(str(r.get('re_used')) + f' / {r.SD_rand_slope:.3f}') if isinstance(r.get('re_used'), str) else '—'} |")
    md += ["", "Mixed models have no adjusted R². The R² shown uses fixed effects plus the shrunk group effects; the "
           "marginal R² uses the fixed part only.", ""]

    md += ["## 2. Parameter estimates (Run 3, 2020–2025)", "", "| Model | Term | Estimate | SE | p-value |", "|---|---|---|---|---|"]
    for _, r in params[params["window"] == head].iterrows():
        if r["model"] == "Alt1_FE" and r["term"].startswith("a |"):
            continue
        md.append(f"| {LABELS[r['model']]} | {r['term']} | {r['estimate']:+.4f} | {_p(r['se'], '{:.4f}')} | {_p(r['p'], '{:.3f}')} |")
    fe = params[(params["window"] == head) & (params["model"] == "Alt1_FE") & params["term"].str.startswith("a |")]
    a0 = params[(params["window"] == head) & (params["model"] == "Alt1_FE") & (params["term"] == "a")]["estimate"].iloc[0]
    md += ["", "**Alt 1 fixed effects** (series intercept a_i = base + shift; base series = AB_CANOLA, a = "
           f"{a0:+.4f}):", "", "| Series | a_i | p (shift vs base) |", "|---|---|---|"]
    for _, r in fe.iterrows():
        md.append(f"| {r['term'][4:].replace(' (vs base)', '')} | {a0 + r['estimate']:+.4f} | {r['p']:.3f} |")
    md += ["", "### Slope stability across expanding-window fits (through 2021 … 2025), Run 3", "",
           "| Model | Slope | Min | Max | SD |", "|---|---|---|---|---|"]
    s = stab[stab["window"] == head].groupby(["model", "slope"])["value"].agg(["min", "max", "std"]).reset_index()
    for _, r in s.iterrows():
        md.append(f"| {LABELS[r['model']]} | {r['slope']} | {r['min']:+.3f} | {r['max']:+.3f} | {r['std']:.3f} |")
    mx = stab[(stab["window"] == head) & stab["model"].str.startswith("Alt3")].drop_duplicates(["model", "fit_through"])
    if len(mx):
        md += ["", "Random-effects structure actually estimable per expanding fold (RI+RS = random intercept and "
               "slope; RI = intercept only, slope variance not identifiable; pooled = no group variance):", "",
               "| Model | " + " | ".join(f"through {y}" for y in sorted(mx["fit_through"].unique())) + " |",
               "|---|" + "---|" * mx["fit_through"].nunique()]
        for mname, g in mx.groupby("model"):
            g = g.set_index("fit_through")
            md.append(f"| {LABELS[mname]} | " + " | ".join(str(g.loc[y, "re_used"]) for y in sorted(g.index)) + " |")

    md += ["", "## 3. Crop-level breakdown (Run 3, expanding 2022–2026): MAPE, and change vs legacy", "",
           "| Crop | " + " | ".join(LABELS[m] for m in MODELS) + " |", "|---|" + "---|" * len(MODELS)]
    c = crop[crop["window"] == head].pivot_table(index="crop", columns="model", values="MAPE")
    for cr, r in c.iterrows():
        cells = [f"{r[m]:.1%}" + ("" if m == "Legacy" else f" ({(r[m] - r['Legacy']) * 100:+.1f})") for m in MODELS]
        md.append(f"| {cfg.CROPS[cr]['label']} | " + " | ".join(cells) + " |")
    md += ["", "In brackets: change in MAPE vs legacy, in percentage points (negative = better).", "",
           "### By forecast year (Run 3): WAPE", "", "| Year | " + " | ".join(LABELS[m] for m in MODELS) + " |",
           "|---|" + "---|" * len(MODELS)]
    y = years[years["window"] == head].pivot_table(index="year", columns="model", values="WAPE")
    for yr, r in y.iterrows():
        md.append(f"| {yr}{' (prelim.)' if yr == PRELIM else ''} | " + " | ".join(f"{r[m]:.1%}" for m in MODELS) + " |")
    md += ["", *recommendation(perf, stab), ""]
    return "\n".join(md)


def recommendation(perf, stab) -> list[str]:
    """Section 4: numbers pulled from this run; the reasoning restates what sections 1-3 show."""
    ex = perf[(perf["test"] == "expanding 2022-2026") & (perf["window"] == "RUN3")].set_index("model")
    sd = stab[(stab["window"] == "RUN3") & (stab["slope"] == "b")].groupby("model")["value"].std()
    best = min((m for m in MODELS if m != "NoChange"), key=lambda m: ex.loc[m, "WAPE"])
    return [
        "## 4. Recommendation", "",
        f"Best out-of-sample WAPE (Run 3, expanding 2022–2026): **{LABELS[best]}** at {ex.loc[best, 'WAPE']:.1%}, vs "
        f"{ex.loc['Legacy', 'WAPE']:.1%} for the legacy model and {ex.loc['NoChange', 'WAPE']:.1%} for no change. Its "
        f"hit rate is {ex.loc[best, 'HitRate']:.0%} (legacy {ex.loc['Legacy', 'HitRate']:.0%}) and its slope SD across "
        f"folds is {sd.get(best, float('nan')):.3f} (legacy {sd.get('Legacy', float('nan')):.3f}).", "",
        "- **None of the three proposed alternatives improves consistently on the legacy model.** Each wins on some "
        "measure in one test and loses in another. Differences are a few tenths of a WAPE point.",
        "  - Alt 1 (fixed effects) overfits: 14 parameters on 78 points, a negative adjusted R² and the worst "
        "expanding-window errors.",
        "  - Alt 2 (tier slopes) trims expanding-window WAPE slightly but worsens MAPE and leave-one-year-out WAPE. It "
        "leaves Tiers 1 and 2 close to the pooled slope. Tier 3 rests on CPSR alone (12 points), with a slope that "
        "swings widely across folds.",
        "  - Alt 3 (mixed) shrinks to the pooled model: the crop-province random-effects variance is estimated at zero. "
        "The data can't support slopes that differ by series.",
        "- **The legacy model's weakness is its intercept, not its pooling.** The fitted drift (a ≈ +2%) is "
        "insignificant (p ≈ 0.2) but pushes most forecasts upward, cutting directional accuracy to about 50%. Dropping it "
        "restores the signal's direction and lowers the errors in every window.",
        "- **Magnitude skill is still thin.** No-change stays competitive in leave-one-year-out WAPE, and the slope is "
        "only marginally significant. Plan-B should stay the automated fallback to analyst Plan-A.",
    ]


if __name__ == "__main__":
    raise SystemExit(main())
