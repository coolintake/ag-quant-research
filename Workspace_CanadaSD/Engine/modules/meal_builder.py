"""
Protein meal balance sheets: Canola Meal and Soymeal (monthly, then annual).

Sources (all KMT; StatCan tonnes x 0.001):
  Crush, Production, Stocks  StatCan 32100352 'Seed crushed', 'Meal produced',
                             'Meal produced (stocks)' (month-end plant stocks)
  Exports, Imports           CIMT meal workbooks (Canola 2306.41, Soy 2304.00)

Marketing years come from the 'Protein Meal' rows of CMFT_Main.csv (Canola
Meal Sep-Aug, Soymeal Oct-Sep). History starts 2015-16 (meal stocks begin
Aug 2015), as for the oils.

Method, per month t:
  Production       = Crush x Yield, Yield = meal produced / seed crushed
  Beginning Stocks = Ending Stocks[t-1]
  Feed & Residual  = Beg + Production + Imports - Exports - End: the balancing
                     plug (domestic disappearance, mostly feed)
  CHECK            = Beg + Prod + Imp - Exp - Feed & Residual - End, |CHECK| <= 1e-6.
Shared inputs and the residual/stock roll come from modules/vegoil_builder.py.
"""

import pandas as pd

from config.paths import DATA_STORE_DIR, MEAL_FACT_TABLE
from config.commodities import PROTEIN_MEALS, VEG_OIL_FIRST_CROP_YEAR, crop_year_label
from modules.vegoil_builder import (
    TOLERANCE, load_crush, cimt_trade, start_months, _crop_year, _residual_plug,
)

PLUG = 'Feed & Residual'
ANNUAL_SUM = ['Crush', 'Production', 'Imports', 'Exports', PLUG]


def meal_start_months():
    return start_months(PROTEIN_MEALS, 'Protein Meal')


def build_meal(cimt_df: pd.DataFrame) -> pd.DataFrame:
    """One row per meal x month, from the first 2015-16 month to the last
    32100352 month. StatCan-suppressed months stay NaN."""
    starts = meal_start_months()
    crush, trade = load_crush(), cimt_trade(cimt_df, PROTEIN_MEALS)
    rows = []
    for meal, cfg in PROTEIN_MEALS.items():
        c = crush.loc[cfg['crush']]
        dates = pd.date_range(pd.Timestamp(VEG_OIL_FIRST_CROP_YEAR, starts[meal], 1),
                              c.index.max(), freq='MS')
        df = pd.DataFrame(index=dates)
        df['Crush'] = c['Crush'].reindex(dates)
        df['Meal_Yield'] = c['Meal Production'].reindex(dates) / df['Crush']
        df['Production'] = df['Crush'] * df['Meal_Yield']
        df['Surveyed Stocks'] = c['Meal Stocks'].reindex(dates)
        df['Opening Survey'] = c['Meal Stocks'].reindex(dates - pd.DateOffset(months=1)).values
        df[['Exports', 'Imports']] = trade[meal].reindex(dates)
        _residual_plug(df, PLUG, [])
        df['Meal'] = meal
        df['Crop_Year'] = [_crop_year(d, starts[meal]) for d in df.index]
        df['CY_Month'] = [(d.month - starts[meal]) % 12 + 1 for d in df.index]
        rows.append(df)
    monthly = pd.concat(rows).rename_axis('Date').reset_index()
    monthly['CHECK'] = (monthly['Beginning Stocks'] + monthly['Production'] + monthly['Imports']
                        - monthly['Exports'] - monthly[PLUG] - monthly['Ending Stocks'])
    cols = ['Date', 'Meal', 'Crop_Year', 'CY_Month', 'Crush', 'Meal_Yield', 'Beginning Stocks',
            'Production', 'Imports', 'Exports', PLUG, 'Ending Stocks', 'Is_Anchor', 'CHECK']
    return monthly[cols]


def annual_meal(monthly: pd.DataFrame) -> pd.DataFrame:
    """One row per meal x complete marketing year (12 months, every flow known,
    closing stock surveyed). Incomplete years are dropped, never estimated."""
    out = []
    for (meal, cy), g in monthly.groupby(['Meal', 'Crop_Year']):
        g = g.sort_values('CY_Month')
        ok = (len(g) == 12 and cy >= VEG_OIL_FIRST_CROP_YEAR
              and g[ANNUAL_SUM + ['Beginning Stocks', 'Ending Stocks']].notna().all().all()
              and bool(g['Is_Anchor'].iloc[-1]))
        if not ok:
            continue
        r = {'Meal': meal, 'Crop_Year': int(cy), **{m: g[m].sum() for m in ANNUAL_SUM},
             'Beginning Stocks': g['Beginning Stocks'].iloc[0],
             'Ending Stocks': g['Ending Stocks'].iloc[-1]}
        r['Yield'] = r['Production'] / r['Crush']
        r['CHECK'] = (r['Beginning Stocks'] + r['Production'] + r['Imports'] - r['Exports']
                      - r[PLUG] - r['Ending Stocks'])
        out.append(r)
    return pd.DataFrame(out)


def run_meal(cimt_df: pd.DataFrame) -> pd.DataFrame:
    """Build, check and save data_store\\CMFT_Meal_Master.csv."""
    monthly = build_meal(cimt_df)
    annual = annual_meal(monthly)
    worst, worst_yr = monthly['CHECK'].abs().max(), annual['CHECK'].abs().max()
    print(f"    Max CHECK residual: monthly {worst:.2e} KMT, annual {worst_yr:.2e} KMT "
          f"(tolerance {TOLERANCE:.0e})")
    if not (worst <= TOLERANCE and worst_yr <= TOLERANCE):
        raise ValueError(f"Protein meal balance exceeds {TOLERANCE} KMT (monthly {worst}, annual {worst_yr})")
    for meal, g in annual.groupby('Meal'):
        yrs = sorted(g['Crop_Year'])
        last_cy = int(monthly.loc[monthly['Meal'] == meal, 'Crop_Year'].max())
        missing = [crop_year_label(y) for y in range(VEG_OIL_FIRST_CROP_YEAR, last_cy + 1)
                   if y not in yrs]
        print(f"    {meal:<11} complete years {crop_year_label(yrs[0])} to {crop_year_label(yrs[-1])}"
              f" ({len(yrs)})" + (f"; incomplete: {missing}" if missing else ""))
        neg = monthly[(monthly['Meal'] == meal) & (monthly[PLUG] < 0)]
        if len(neg):
            print(f"      NOTE: {PLUG} < 0 in {len(neg)} months "
                  f"(min {neg[PLUG].min():.1f} KMT, {neg.loc[neg[PLUG].idxmin(), 'Date']:%Y-%m})")
    DATA_STORE_DIR.mkdir(parents=True, exist_ok=True)
    monthly.to_csv(MEAL_FACT_TABLE, index=False)
    print(f"    [OK] Written {len(monthly)} rows to {MEAL_FACT_TABLE.name}")
    return monthly
