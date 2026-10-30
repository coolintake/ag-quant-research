"""Run-specific, anchor-tiered log-log acreage elasticity calibration (model_crop_wallet_step1.md 6-7).

Per province p, pricing run w and pair (crop j, anchor A) from settings.TIERS (tier 0: CWRS on the
inverted CWRS/Canola ratio; tier 1: vs CWRS; tier 2: Lentils vs Durum, Soybeans and Flax vs Canola):

    y_t = ln(Acres_{j,t} / Acres_{j,t-1})
    x_t = ln(R_{j/A,t} / Rbar_{j/A,t-1})     R = RPA_j / RPA_A;  Rbar = mean R over t-5 .. t-1 (same run)
    y_t = alpha_0 + beta_j * x_t + e_t        (OLS)

RPA = expected (trend) yield x price - (seed + fertilizer + chemicals).

Guardrails
- A ratio is NULL when either RPA is missing or <= 0 (log undefined). Nothing is filled.
- beta is estimated by OLS, then reported as the bounded least-squares estimate on [0.10, 2.00]
  (7.2). For one regressor the SSE is convex in beta, so that estimate is the OLS beta clipped
  to the interval, with alpha re-solved at the clipped beta. No multipliers are applied.
- beta_OLS <= 0 is flagged INVALID_INVERTED_SLOPE: the calibrated elasticity is the 0.10 floor
  with alpha re-fitted, the negative value is never reported, and the fit is excluded from the
  diagnostic window ranking.
- N < 3 or no variation in x: UNCALIBRATED (no parameters; acres are held at T-1 in forecasts).
- Regional exclusions (settings.REGIONAL_EXCLUSIONS, e.g. MB Durum, AB Soybeans) are never
  estimated: status EXCLUDED_REGIONAL_CROP, acres held at T-1.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import statsmodels.api as sm

import settings as S

FIT_OK = ("OK", "LOW_N")


# --------------------------------------------------------------------------- #
# Ratios for one province x run cell
# --------------------------------------------------------------------------- #
def build_cell_ratios(cell: pd.DataFrame) -> pd.DataFrame:
    """RPA ratios, 5-year baselines and log deviations for every pair, one province and run."""
    cols = ["year", "acres", "acres_prev", "exp_yield", "price", "price_source", "variable_cost", "rpa"]
    frames = []
    for crop, anchor in S.PAIRS:
        j = cell.loc[cell["crop"] == crop, cols]
        a = cell.loc[cell["crop"] == anchor, cols]
        m = j.merge(a, on="year", suffixes=("_j", "_a")).sort_values("year", ignore_index=True)
        m.insert(0, "pair", f"{crop}/{anchor}")

        reasons = []
        for _, r in m.iterrows():
            if pd.notna(r["rpa_j"]) and pd.notna(r["rpa_a"]):
                reasons.append("RPA <= 0 (log undefined)" if min(r["rpa_j"], r["rpa_a"]) <= 0 else None)
                continue
            parts = []
            for side, name in (("j", crop), ("a", anchor)):
                if pd.isna(r[f"price_{side}"]):
                    parts.append(f"{name} price {r[f'price_source_{side}']}")
                if pd.isna(r[f"variable_cost_{side}"]):
                    parts.append(f"{name} no budget")
                if pd.isna(r[f"exp_yield_{side}"]):
                    parts.append(f"{name} no StatCan yield")
            reasons.append("; ".join(parts))
        m["ratio_null_reason"] = reasons
        m["ratio"] = np.where(m["ratio_null_reason"].isna(), m["rpa_j"] / m["rpa_a"], np.nan)

        series = m.set_index("year")["ratio"]
        base, n_base = [], []
        for year in m["year"]:
            window = series.reindex(range(year - S.BASELINE_LOOKBACK, year)).dropna()
            base.append(window.mean() if len(window) >= S.BASELINE_MIN_YEARS else np.nan)
            n_base.append(len(window))
        m["ratio_baseline"] = base
        m["baseline_n"] = n_base
        m["x_dev"] = np.log(m["ratio"] / m["ratio_baseline"])
        ok = (m["acres_j"] > 0) & (m["acres_prev_j"] > 0)
        m["y_dlog_acres"] = np.where(ok, np.log(m["acres_j"].where(ok) / m["acres_prev_j"].where(ok)), np.nan)
        frames.append(m)
    return pd.concat(frames, ignore_index=True)


# --------------------------------------------------------------------------- #
# Estimation
# --------------------------------------------------------------------------- #
def bound_flag(beta_ols: float) -> str:
    lo, hi = S.BETA_BOUNDS
    if beta_ols <= 0:
        return "INVALID_INVERTED_SLOPE"
    if beta_ols < lo:
        return "AT_LOWER_BOUND"
    if beta_ols > hi:
        return "AT_UPPER_BOUND"
    return "INTERIOR"


def fit(x: np.ndarray, y: np.ndarray) -> dict:
    n = len(x)
    if n < S.MIN_OBS_FIT:
        return {"n": n, "status": "UNCALIBRATED", "reason": f"N = {n} < {S.MIN_OBS_FIT}"}
    if np.ptp(x) == 0:
        return {"n": n, "status": "UNCALIBRATED", "reason": "no variation in x"}
    model = sm.OLS(y, sm.add_constant(x)).fit()
    alpha, beta = (float(v) for v in model.params)
    beta_b = float(np.clip(beta, *S.BETA_BOUNDS))
    alpha_b = float(y.mean() - beta_b * x.mean())
    sst = float(((y - y.mean()) ** 2).sum())
    return {
        "n": n,
        "status": "OK" if n >= S.LOW_CONFIDENCE_N else "LOW_N",
        "flag": bound_flag(beta),
        "beta_bounded": beta_b,
        "alpha0_bounded": alpha_b,
        "r2_bounded": 1 - float(((y - alpha_b - beta_b * x) ** 2).sum()) / sst if sst > 0 else np.nan,
        "beta_ols": beta,                         # audit column; never shown when inverted
        "alpha0_ols": alpha,
        "beta_se": float(model.bse[1]), "alpha0_se": float(model.bse[0]),
        "beta_p": float(model.pvalues[1]), "alpha0_p": float(model.pvalues[0]),
        "r2": float(model.rsquared), "adj_r2": float(model.rsquared_adj),
        "resid_se": float(np.sqrt(model.scale)),
    }


def _excluded_pair(province: str, pair: str) -> bool:
    return S.is_excluded(province, pair.split("/")[0])


def _excluded_fit(province: str) -> dict:
    return {"n": 0, "status": S.EXCLUDED_STATUS, "reason": f"regional exclusion in {province}"}


def fit_cell(ratios: pd.DataFrame, province: str, run: str) -> pd.DataFrame:
    """Own-sample fit of every pair in one province x run cell (regional exclusions skipped)."""
    rows = []
    for pair, g in ratios.groupby("pair", sort=False):
        if _excluded_pair(province, pair):
            rows.append({"province": province, "run": run, "pair": pair, "sample": "own", "years": "",
                         **_excluded_fit(province)})
            continue
        ok = g.dropna(subset=["x_dev", "y_dlog_acres"])
        rows.append({"province": province, "run": run, "pair": pair, "sample": "own",
                     "years": ",".join(map(str, ok["year"])),
                     **fit(ok["x_dev"].to_numpy(), ok["y_dlog_acres"].to_numpy())})
    return pd.DataFrame(rows)


def fit_common(ratios: pd.DataFrame, province: str) -> pd.DataFrame:
    """Refit Run 1-3 for one province on the years all three runs share, per pair.

    R^2 is only comparable across windows on an identical sample.
    """
    rows = []
    usable = ratios.dropna(subset=["x_dev", "y_dlog_acres"])
    for pair, g in usable.groupby("pair", sort=False):
        if _excluded_pair(province, pair):
            continue
        years = set.intersection(*(set(g.loc[g["run"] == w, "year"]) for w in S.WINDOWS))
        for run in S.WINDOWS:
            sub = g[(g["run"] == run) & g["year"].isin(years)].sort_values("year")
            rows.append({"province": province, "run": run, "pair": pair, "sample": "common",
                         "years": ",".join(map(str, sorted(years))),
                         **fit(sub["x_dev"].to_numpy(), sub["y_dlog_acres"].to_numpy())})
    return pd.DataFrame(rows)


def _valid(fits: pd.DataFrame) -> pd.DataFrame:
    """Fits eligible for window selection: estimated and beta_OLS > 0 (not inverted)."""
    if fits.empty or "beta_ols" not in fits:
        return fits.iloc[0:0]
    return fits[fits["status"].isin(FIT_OK) & (fits["beta_ols"] > 0)]


def select_windows(common: pd.DataFrame, own: pd.DataFrame, province: str) -> pd.DataFrame:
    """Winning run per pair: highest R^2 of the calibrated (bounded) fit among valid (beta_OLS > 0)
    fits; ties -> lower beta SE. For in-bounds fits this R^2 equals the OLS R^2.

    Uses the common sample; where the three runs share fewer than MIN_OBS_FIT years, the runs'
    own samples are used and the basis says so.
    """
    out = []
    for crop, anchor in S.PAIRS:
        pair = f"{crop}/{anchor}"
        if S.is_excluded(province, crop):
            out.append({"province": province, "pair": pair, "winning_run": None, "basis": S.EXCLUDED_STATUS})
            continue
        c = common[common["pair"] == pair] if not common.empty else common
        if not c.empty and c["status"].isin(FIT_OK).any():
            cand, basis = _valid(c), "common"
        else:
            cand, basis = _valid(own[own["pair"] == pair]), "own"
        row = {"province": province, "pair": pair, "winning_run": None, "basis": basis}
        if cand.empty:
            est = own[(own["pair"] == pair) & own["status"].isin(FIT_OK)]
            row["basis"] = "no valid fit: all runs inverted" if not est.empty else "not estimable"
        else:
            best = cand.sort_values(["r2_bounded", "beta_se"], ascending=[False, True]).iloc[0]
            row.update(winning_run=best["run"], r2=best["r2_bounded"], n=best["n"])
        out.append(row)
    return pd.DataFrame(out)


def province_window(selection: pd.DataFrame, common: pd.DataFrame, own: pd.DataFrame) -> dict:
    """Province-level winner: most pair wins among valid fits; tie -> highest mean valid R^2."""
    wins = selection["winning_run"].value_counts()
    scores = []
    for run in S.WINDOWS:
        pool = []
        for _, s in selection.dropna(subset=["winning_run"]).iterrows():
            src = common if s["basis"] == "common" else own
            v = _valid(src[(src["pair"] == s["pair"]) & (src["run"] == run)])
            pool += list(v["r2_bounded"])
        scores.append((int(wins.get(run, 0)), float(np.mean(pool)) if pool else -1.0, len(pool), run))
    best = max(scores)
    return {"winning_run": best[3] if best[0] > 0 else None,
            "wins": {s[3]: s[0] for s in scores},
            "mean_valid_r2": {s[3]: (s[1] if s[2] else np.nan) for s in scores},
            "n_valid": {s[3]: s[2] for s in scores}}


def fit_vintages(ratios: pd.DataFrame, vintages=S.VINTAGES) -> pd.DataFrame:
    """Living-calibration vintages: for crop year V, refit every province x run x pair on t <= V.

    Baselines only use earlier years, so a vintage never sees later data. Vintage = last
    calibration year equals the production parameters (fits_own_sample).
    """
    frames = []
    for v in vintages:
        sub = ratios[ratios["year"] <= v]
        for (prov, run), cell in sub.groupby(["province", "run"], sort=False):
            f = fit_cell(cell, prov, run)
            f.insert(0, "vintage", v)
            frames.append(f)
    return pd.concat(frames, ignore_index=True)


def forecast_action(status: str) -> str:
    """Spec 8.2: apply the elasticity, or hold acres at T-1."""
    return "APPLY_ELASTICITY" if status in FIT_OK else "HOLD_T-1"


FLOOR_FLAGS = ("AT_LOWER_BOUND", "INVALID_INVERTED_SLOPE")


def average_vintages(vintage_fits: pd.DataFrame, vintages=None) -> pd.DataFrame:
    """Indicative elasticity: mean calibrated beta over the last AVERAGE_VINTAGES vintages.

    Per province x run x pair. Only estimated vintages enter the mean (status in FIT_OK);
    a regional exclusion stays EXCLUDED_REGIONAL_CROP; no estimated vintage -> UNCALIBRATED.
    floor_count = vintages whose beta sits on the 0.10 floor. The vintages are expanding-window
    fits that share years, so the mean is a smoothed indication, not an average of independent
    estimates.
    """
    vintages = list(vintages or S.VINTAGES[-S.AVERAGE_VINTAGES:])
    sub = vintage_fits[vintage_fits["vintage"].isin(vintages)]
    rows = []
    for (prov, run, pair), g in sub.groupby(["province", "run", "pair"], sort=False):
        rec = {"province": prov, "run": run, "pair": pair, "vintages": f"{vintages[0]}-{vintages[-1]}"}
        if (g["status"] == S.EXCLUDED_STATUS).any():
            rows.append({**rec, "status": S.EXCLUDED_STATUS, "n_vintages": 0})
            continue
        est = g[g["status"].isin(FIT_OK)].sort_values("vintage")
        if est.empty:
            rows.append({**rec, "status": "UNCALIBRATED", "n_vintages": 0})
            continue
        floors = int(est["flag"].isin(FLOOR_FLAGS).sum())
        rows.append({**rec, "status": "AVERAGED", "n_vintages": len(est),
                     "beta_avg": est["beta_bounded"].mean(), "beta_min": est["beta_bounded"].min(),
                     "beta_max": est["beta_bounded"].max(), "alpha0_avg": est["alpha0_bounded"].mean(),
                     "r2_avg": est["r2_bounded"].mean(), "floor_count": floors,
                     "inverted_count": int((est["flag"] == "INVALID_INVERTED_SLOPE").sum()),
                     "detail": "; ".join(f"{int(r.vintage)}: {r.beta_bounded:.3f} {r.flag}"
                                         for r in est.itertuples())})
    return pd.DataFrame(rows)
