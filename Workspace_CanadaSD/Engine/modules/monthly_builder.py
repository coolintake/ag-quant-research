"""
Monthly S&D builder (ENGINE_RULES sections 4 and 5).

Input: StatCan 32100013 in wide form, one row per commodity x release
(Dec / Mar / Jul), values CUMULATIVE over the crop year to that release.

Crop-year month index k (1..12) of each release:
    Aug-start crops: Dec -> 5, Mar -> 8, Jul -> 12    (the 5/3/4 rule)
    Soybeans (Sep):  Dec -> 4, Mar -> 7, Jul -> 12    (Jul release = Sep-Aug)

Per crop year:
  * Production enters in month 1 (new crop joins supply at harvest).
  * Flows (Imports, Food, Industrial, Crush, Exports) are split evenly
    inside each release-to-release segment:
        months 1-5  = cum(Dec) / 5
        months 6-8  = (cum(Mar) - cum(Dec)) / 3
        months 9-12 = (cum(Jul) - cum(Mar)) / 4
    A missing release merges its two segments (still an even split).
  * FSR, two modes (FSR_MODE):
      'statcan'  (STOCKS_MODE = 'rolled'; matches the hand-built Wheat_Mo):
                 StatCan's reported cumulative FSR (seed + loss + feed) is
                 split 5/3/4 like the other flows, and stocks are DERIVED by
                 rolling the flows forward. July stocks then equal StatCan's
                 except for any CIMT-vs-StatCan import difference.
      'residual' (STOCKS_MODE = 'anchored', current default): FSR is the
                 residual that lands stocks exactly on each StatCan stock
                 observation (Dec/Mar/Jul), spread evenly within each period.
  * Exports & Imports (config.commodities.TRADE_SOURCE):
      'cimt' (current): CIMT monthly actuals ONLY, the observed value in each
             calendar month. StatCan 32100013 trade lines are ignored. Crop
             years with no CIMT coverage are left empty and reported.
      'statcan': StatCan cumulative releases split 5/3/4.
  * Stocks roll month to month: Beg[t] = End[t-1]; the first month of each
    crop year is bridged from the previous crop year's month 12
    (ENGINE_RULES 5.2 and 5.3). The first crop year starts from StatCan's
    reported beginning stocks (5.1).
  * Months after the last release that has ending stocks are not emitted
    (e.g. the current crop year stops at the latest StatCan release). A
    crop year with no stock observation at all is skipped, and the chain
    restarts from reported beginning stocks the following year.
"""

from typing import Dict, Optional, Tuple
import numpy as np
import pandas as pd

from config.commodities import crop_year_start, BUSHEL_MULTIPLIERS, TRADE_SOURCE, STOCKS_MODE

# Set in config/commodities.py (STOCKS_MODE): anchored -> residual FSR
FSR_MODE = 'residual' if STOCKS_MODE == 'anchored' else 'statcan'

FLOW_METRICS = ['Imports', 'Food', 'Industrial', 'Crush', 'Exports']
DEMAND_METRICS = ['Food', 'Industrial', 'Crush', 'Exports']
OUTPUT_COLS = [
    'Date', 'Date_Str', 'Commodity_Norm', 'Crop_Year', 'CY_Month',
    'Beginning Stocks', 'Production', 'Imports',
    'Food', 'Industrial', 'Crush', 'FSR', 'Exports', 'Ending Stocks',
    'Planted_MLN_AC', 'Harvested_MLN_AC', 'Yield_BU_AC', 'Is_Anchor',
]


def release_index(ref_month: int, start_month: int) -> int:
    """Crop-year month index (1..12) covered by a release dated ref_month."""
    if ref_month == 7:           # July release is always the full crop year
        return 12
    return (ref_month - start_month) % 12 + 1


def calendar_date(crop_year: int, k: int, start_month: int) -> pd.Timestamp:
    month = (start_month + k - 2) % 12 + 1
    year = crop_year if month >= start_month else crop_year + 1
    return pd.Timestamp(year=year, month=month, day=1)


def _spread(points: Dict[int, float], upto: int) -> Dict[int, float]:
    """Even split of a cumulative series between known points.
    points: {k: cumulative value}; 0 -> 0 is implied. Returns {month: value}
    for months 1..min(upto, last known k)."""
    known = sorted((k, v) for k, v in points.items() if pd.notna(v))
    out = {}
    prev_k, prev_v = 0, 0.0
    for k, v in known:
        if k > upto:
            break
        for m in range(prev_k + 1, k + 1):
            out[m] = (v - prev_v) / (k - prev_k)
        prev_k, prev_v = k, v
    return out


def build_monthly(
    sd_releases: pd.DataFrame,
    harvested: Optional[pd.DataFrame] = None,
    cimt_monthly: Optional[pd.DataFrame] = None,
    fsr_mode: str = FSR_MODE,
    trade_source: Optional[str] = None,
) -> pd.DataFrame:
    """
    sd_releases : wide StatCan rows (Date, Commodity_Norm, Crop_Year, metrics)
    harvested   : Crop_Year, Commodity_Norm, Harvested_MLN_AC [, Planted_MLN_AC]
    cimt_monthly: Date, Commodity_Norm, Exports, Imports (monthly KMT)
    """
    harv, plant = {}, {}
    if harvested is not None and len(harvested):
        for r in harvested.itertuples(index=False):
            key = (r.Commodity_Norm, int(r.Crop_Year))
            harv[key] = getattr(r, 'Harvested_MLN_AC', np.nan)
            plant[key] = getattr(r, 'Planted_MLN_AC', np.nan)
    cimt = {}
    if cimt_monthly is not None and len(cimt_monthly):
        for r in cimt_monthly.itertuples(index=False):
            cimt[(r.Commodity_Norm, pd.Timestamp(r.Date))] = {
                'Exports': getattr(r, 'Exports', np.nan),
                'Imports': getattr(r, 'Imports', np.nan),
            }

    if trade_source is None:
        trade_source = TRADE_SOURCE
    missing_trade = set()
    rows = []
    for comm, g in sd_releases.groupby('Commodity_Norm'):
        start = crop_year_start(comm)
        prev_end = None
        prev_cy = None
        for cy in sorted(g['Crop_Year'].dropna().astype(int).unique()):
            rel = {}
            for _, r in g[g['Crop_Year'] == cy].iterrows():
                rel[release_index(r['Date'].month, start)] = r

            def cum(metric):
                return {k: r.get(metric, np.nan) for k, r in rel.items()}

            stock_obs = {k: v for k, v in cum('Ending Stocks').items() if pd.notna(v)}
            if not stock_obs:
                prev_end, prev_cy = None, None      # break the chain
                continue
            last_k = max(stock_obs)

            # Production / reported beginning stocks: latest release carrying them
            def latest(metric):
                vals = [(k, v) for k, v in cum(metric).items() if pd.notna(v)]
                return max(vals)[1] if vals else np.nan
            production = latest('Production')
            reported_beg = latest('Beginning Stocks')

            bridged = prev_end is not None and prev_cy == cy - 1
            beg = prev_end if bridged else reported_beg
            if pd.isna(beg):
                beg = 0.0

            flows = {m: _spread(cum(m), last_k) for m in FLOW_METRICS}

            months_dates = {k: calendar_date(cy, k, start) for k in range(1, last_k + 1)}
            if trade_source == 'cimt':
                # Exports & Imports: CIMT monthly actuals ONLY (StatCan trade
                # lines ignored). A crop year CIMT covers gets its observed
                # value in each calendar month, 0 for a month with no rows
                # (no trade reported). A crop year CIMT does not cover at all
                # is left empty (NaN) and reported, never estimated.
                cy_cimt = {k: cimt.get((comm, d)) for k, d in months_dates.items()}
                for m in ('Exports', 'Imports'):
                    if any(c is not None for c in cy_cimt.values()):
                        flows[m] = {k: (c[m] if c is not None and pd.notna(c[m]) else 0.0)
                                    for k, c in cy_cimt.items()}
                    else:
                        flows[m] = {}
                        missing_trade.add((comm, cy))

            def fv(m, k):
                v = flows[m].get(k, np.nan)
                return 0.0 if pd.isna(v) else v

            if fsr_mode == 'statcan':
                # Reported cumulative FSR split 5/3/4; stocks derived
                fsr_spread = _spread(cum('FSR'), last_k)
                fsr = {k: fsr_spread.get(k, 0.0) for k in range(1, last_k + 1)}
                anchors_to_snap = set()
            else:
                # Residual FSR per stock segment -> stocks hit observations
                fsr = {}
                seg_start, seg_beg = 1, beg
                for k_end in sorted(stock_obs):
                    months = range(seg_start, k_end + 1)
                    prod_seg = production if (seg_start == 1 and pd.notna(production)) else 0.0
                    supply = seg_beg + prod_seg + sum(fv('Imports', k) for k in months)
                    demand = sum(fv(m, k) for m in DEMAND_METRICS for k in months)
                    seg_fsr = supply - demand - stock_obs[k_end]
                    for k in months:
                        fsr[k] = seg_fsr / len(months)
                    seg_start, seg_beg = k_end + 1, stock_obs[k_end]
                anchors_to_snap = set(stock_obs)

            # Roll stocks forward month by month
            h = harv.get((comm, cy), np.nan)
            pl = plant.get((comm, cy), np.nan)
            b = beg
            for k in range(1, last_k + 1):
                prod_k = production if (k == 1 and pd.notna(production)) else 0.0
                end = (b + prod_k + fv('Imports', k)
                       - sum(fv(m, k) for m in DEMAND_METRICS) - fsr[k])
                if k in anchors_to_snap:     # remove float drift at anchors
                    end = stock_obs[k]
                d = calendar_date(cy, k, start)
                y = np.nan
                if k == 1 and pd.notna(h) and h > 0 and pd.notna(production) and comm in BUSHEL_MULTIPLIERS:
                    y = production * 1000 / (h * 1_000_000) * BUSHEL_MULTIPLIERS[comm]
                rows.append({
                    'Date': d, 'Date_Str': d.strftime('%Y-%m-%d'),
                    'Commodity_Norm': comm, 'Crop_Year': cy, 'CY_Month': k,
                    'Beginning Stocks': b,
                    'Production': prod_k,
                    **{m: flows[m].get(k, np.nan) for m in FLOW_METRICS},
                    'FSR': fsr[k],
                    'Ending Stocks': end,
                    'Planted_MLN_AC': pl if k == 1 else np.nan,
                    'Harvested_MLN_AC': h if k == 1 else np.nan,
                    'Yield_BU_AC': y,
                    'Is_Anchor': k in stock_obs,
                })
                b = end
            prev_end = b if last_k == 12 else None   # only bridge from a complete year
            prev_cy = cy if last_k == 12 else None

    if missing_trade:
        by_comm = {}
        for c, y in sorted(missing_trade):
            by_comm.setdefault(c, []).append(y)
        print("    WARNING: no CIMT trade for these crop years -> Exports/Imports left "
              "EMPTY (stocks overstated by the missing exports):")
        for c, ys in by_comm.items():
            print(f"      {c}: {ys[0]}..{ys[-1]} ({len(ys)} crop years)")
    out = pd.DataFrame(rows, columns=OUTPUT_COLS)
    return out.sort_values(['Commodity_Norm', 'Date']).reset_index(drop=True)
