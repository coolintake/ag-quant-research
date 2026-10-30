"""Plan-A ingestion, Plan-B pooled regression and evaluation (SPECIFICATION_V2.md 6).

Plan-B, per window w: one OLS pooled over provinces (not the Prairie aggregate) and crops (CWRS on its
Canola signal), through the origin:  %dAcres = b_w * %dSignal + e  (intercept forced to 0, no other
constraint). The 2020-2025 backtest (analysis/backtest_plan_b.py) showed that the fitted drift term was
insignificant but biased forecasts upward and halved directional accuracy. b_w <= 0 -> PLAN_B_NO_SIGNAL
(the window's Plan-B is not used). Out-of-sample: the prediction for year T is fitted on years < T.
Plan-B is the automated fallback / prior behind analyst Plan-A (SPECIFICATION_V2.md 6).

Evaluation: directional hit rate (sign of %dSignal vs %dAcres), mean Spearman rank correlation
across crops within each province-year, and WAPE of Plan-A / Plan-B / no-change against CANSIM.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy.stats import spearmanr

import config as C

PLAN_A_COLUMNS = ["province", "crop", "year", "window", "pct_change", "rationale", "author", "date"]
MIN_FIT_OBS = 12


def _obs(panel: pd.DataFrame, run: str, years) -> pd.DataFrame:
    d = panel[(panel["province"].isin(C.PROVINCES)) & (panel["run"] == run) & panel["year"].isin(years)]
    return d.dropna(subset=["pct_d_signal", "pct_d_acres"])


def fit_pooled(obs: pd.DataFrame) -> dict:
    n = len(obs)
    if n < MIN_FIT_OBS:
        return {"n": n, "status": "INSUFFICIENT_DATA"}
    y, x = obs["pct_d_acres"].to_numpy(), obs["pct_d_signal"].to_numpy()
    m = sm.OLS(y, x).fit()                                   # no constant: regression through the origin
    b = float(m.params[0])
    # statsmodels reports an uncentered R^2 without a constant; report the centered one so it is
    # comparable with the earlier intercept model (it can be slightly negative).
    r2 = 1 - float(((y - b * x) ** 2).sum()) / float(((y - y.mean()) ** 2).sum())
    return {"n": n, "status": "OK", "a": 0.0, "b": b, "b_se": float(m.bse[0]), "b_p": float(m.pvalues[0]),
            "r2": r2, "flag": "PLAN_B_NO_SIGNAL" if b <= 0 else None}


def plan_b(panel: pd.DataFrame, last_actual: int, forecast_year: int):
    """Full-sample fits (2020..last_actual), out-of-sample predictions (every year 2022..forecast_year)."""
    fits, preds, obs_all = [], [], []
    for run in C.WINDOWS:
        sample = _obs(panel, run, range(C.YEARS[0] + 1, last_actual + 1))
        obs_all.append(sample.assign(window=run))
        fits.append({"window": run, "years": f"{C.YEARS[0] + 1}-{last_actual}", **fit_pooled(sample)})
        for t in range(C.YEARS[0] + 3, forecast_year + 1):
            f = fit_pooled(_obs(panel, run, range(C.YEARS[0] + 1, t)))
            target = panel[(panel["province"].isin(C.PROVINCES)) & (panel["run"] == run) & (panel["year"] == t)]
            for _, r in target.iterrows():
                usable = f["status"] == "OK" and pd.notna(r["pct_d_signal"]) and pd.notna(r["acres_prev"])
                pct = f["a"] + f["b"] * r["pct_d_signal"] if usable else np.nan
                preds.append({"window": run, "year": t, "province": r["province"], "crop": r["crop"],
                              "fit_years": f"{C.YEARS[0] + 1}-{t - 1}", "fit_n": f["n"], "b": f.get("b"),
                              "plan_b_flag": f.get("flag"), "pct_d_signal": r["pct_d_signal"],
                              "pct_plan_b": pct, "acres_prev": r["acres_prev"], "acres_actual": r["acres"],
                              "acres_plan_b": r["acres_prev"] * (1 + pct) if usable else np.nan})
    return pd.DataFrame(fits), pd.DataFrame(preds), pd.concat(obs_all, ignore_index=True)


def load_plan_a(path=C.PLAN_A_CSV) -> pd.DataFrame:
    """Analyst assignments; writes an empty template (header only) on first use."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(columns=PLAN_A_COLUMNS).to_csv(path, index=False)
    a = pd.read_csv(path)
    missing = set(PLAN_A_COLUMNS) - set(a.columns)
    if missing:
        raise ValueError(f"{path.name} is missing columns {sorted(missing)}")
    return a


def apply_plan_a(plan_a: pd.DataFrame, panel: pd.DataFrame) -> pd.DataFrame:
    if plan_a.empty:
        return pd.DataFrame(columns=["province", "crop", "year", "window", "pct_change", "acres_prev",
                                     "acres_plan_a", "acres_actual"])
    base = panel[panel["province"].isin(C.PROVINCES)].drop_duplicates(["province", "crop", "year"])
    m = plan_a.merge(base[["province", "crop", "year", "acres_prev", "acres"]], on=["province", "crop", "year"],
                     how="left")
    m["acres_plan_a"] = m["acres_prev"] * (1 + m["pct_change"])
    return m.rename(columns={"acres": "acres_actual"})


def _wape(df: pd.DataFrame, col: str) -> float:
    d = df.dropna(subset=[col, "acres_actual"])
    return float((d[col] - d["acres_actual"]).abs().sum() / d["acres_actual"].sum()) if len(d) else np.nan


def evaluate(panel: pd.DataFrame, preds: pd.DataFrame, plan_a_rows: pd.DataFrame) -> dict:
    prov = panel[panel["province"].isin(C.PROVINCES)].dropna(subset=["pct_d_signal", "pct_d_acres"])
    hit = (prov.assign(hit=np.sign(prov["pct_d_signal"]) == np.sign(prov["pct_d_acres"]))
               .groupby("run")["hit"].agg(["mean", "count"]).rename(columns={"mean": "hit_rate", "count": "n"}))
    hit_crop = (prov.assign(hit=np.sign(prov["pct_d_signal"]) == np.sign(prov["pct_d_acres"]))
                    .groupby(["run", "crop"])["hit"].mean().unstack())
    rho = []
    for (run, p, y), g in prov.groupby(["run", "province", "year"]):
        if len(g) >= 3 and g["pct_d_signal"].nunique() > 1 and g["pct_d_acres"].nunique() > 1:
            rho.append({"run": run, "province": p, "year": y, "rho": spearmanr(g["pct_d_signal"], g["pct_d_acres"])[0]})
    rho = pd.DataFrame(rho)
    p = preds.assign(acres_nochange=preds["acres_prev"])
    wape = (p.groupby(["window", "year"]).apply(lambda g: pd.Series({
        "n": int(g.dropna(subset=["acres_plan_b", "acres_actual"]).shape[0]),
        "wape_plan_b": _wape(g, "acres_plan_b"),
        "wape_nochange": _wape(g.dropna(subset=["acres_plan_b"]), "acres_nochange")}), include_groups=False)
        .reset_index())
    return {"hit_rate": hit.reset_index(), "hit_by_crop": hit_crop.reset_index(), "spearman": rho, "wape": wape,
            "plan_a_wape": _wape(plan_a_rows, "acres_plan_a") if len(plan_a_rows) else np.nan}
