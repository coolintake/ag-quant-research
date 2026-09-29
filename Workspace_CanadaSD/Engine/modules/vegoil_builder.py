"""
Vegetable oil balance sheets: Canola Oil and Soy Oil (monthly, then annual).

Sources (all KMT; StatCan tonnes x 0.001):
  Crush, Production, Stocks  StatCan 32100352 'Seed crushed', 'Oil produced',
                             'Oil produced (stocks)' (month-end plant stocks)
  Exports, Imports           CIMT oil workbooks (crude + refined HS lines)
  Biofuel feedstock          StatCan 25100082 'Vegetable oils, total' / 'Inputs'

Marketing years come from the 'Vegetable Oil' rows of CMFT_Main.csv
(Canola Oil Sep-Aug, Soy Oil Oct-Sep). History starts 2015-16, the first oil
year that opens from a surveyed stock (32100352 oil stocks begin Aug 2015).

Method, per month t:
  Production        = Crush x Yield, with Yield = StatCan oil produced / seed
                      crushed (so Production equals StatCan oil produced)
  Beginning Stocks  = Ending Stocks[t-1]
  Industrial        = 25100082 feedstock total x proxy weight
      Apparent supply[oil] = Production + Imports - Exports   (floored at 0)
      Weight[Canola Oil]   = AS[canola] / (AS[canola] + AS[soy]);  Weight[Soy] = 1 - that
      Before Jan 2020 (no 25100082 data) the feedstock total is flatlined at
      the FEEDSTOCK_BASELINE_YEAR monthly average; weights stay monthly.
      Months where one oil is suppressed by StatCan ('x') carry the trailing
      12-month average weight forward.
  Food (Residual)   = Beg + Production + Imports - Exports - Industrial - End,
                      the balancing plug. A month with no surveyed stock
                      shares its segment's residual evenly (e.g. Soy Oil
                      Nov 2018), so stocks still land on every survey.
  CHECK             = Beg + Prod + Imp - Exp - Ind - Food - End, |CHECK| <= 1e-6.
"""

from typing import Dict, Tuple

import numpy as np
import pandas as pd

from config.paths import STATCAN_DIR, DATA_STORE_DIR, VEGOIL_FACT_TABLE
from config.commodities import (
    VEG_OILS, VEG_OIL_FIRST_CROP_YEAR, FEEDSTOCK_BASELINE_YEAR, crop_year_label,
)
from config.mappings import load_dictionary_mapping

CRUSH_CSV = STATCAN_DIR / "Crush_32100352-eng" / "32100352.csv"
FEEDSTOCK_CSV = STATCAN_DIR / "Feedstock_25100082-eng" / "25100082.csv"
TONNES_TO_KMT = 0.001
TOLERANCE = 1e-6                     # max |CHECK| per month and per year (KMT)

_MONTH_NUM = {m: i + 1 for i, m in enumerate(
    ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'])}
CRUSH_PROCESS = {'Seed crushed': 'Crush', 'Oil produced': 'Production',
                 'Oil produced (stocks)': 'Stocks',
                 'Meal produced': 'Meal Production', 'Meal produced (stocks)': 'Meal Stocks'}


# ------------------------------------------------------------------ inputs
# (shared with modules/meal_builder.py)
def start_months(registry: Dict, group: str) -> Dict[str, int]:
    """Marketing-year start month per product, from the CMFT_Main.csv rows of
    Commodity_Group `group` (e.g. 'Sep-Aug' -> 9)."""
    m = load_dictionary_mapping()
    rows = m[m['Commodity_Group'].astype(str).str.strip() == group]
    out = {}
    for key, cfg in registry.items():
        labels = rows.loc[rows['Source Commodity'].astype(str).str.strip() == cfg['cmft_source'],
                          'Crop_Year'].dropna().astype(str).str.strip().unique()
        if len(labels) != 1:
            raise ValueError(f"CMFT_Main.csv: expected one {group} Crop_Year for "
                             f"'{cfg['cmft_source']}' ({key}), found {list(labels)}")
        out[key] = _MONTH_NUM[labels[0].split('-')[0].strip()[:3].upper()]
    return out


def oil_start_months() -> Dict[str, int]:
    return start_months(VEG_OILS, 'Vegetable Oil')


def _require_units(df: pd.DataFrame, uom: str, table: str) -> None:
    bad = df[(df['UOM'] != uom) | (df['SCALAR_FACTOR'].str.strip().str.lower() != 'units')]
    if len(bad):
        raise ValueError(f"{table}: expected UOM '{uom}' with scalar 'units', found "
                         f"{bad[['UOM', 'SCALAR_FACTOR']].drop_duplicates().values.tolist()}")


def load_crush() -> pd.DataFrame:
    """32100352, Canada: one row per (crush commodity, month); Crush /
    Production / Stocks in KMT. StatCan-suppressed values ('x') stay NaN."""
    df = pd.read_csv(CRUSH_CSV, dtype=str, low_memory=False)
    df['Process'] = df['Process'].str.split().str.join(' ')      # 'Oil  produced'
    crushers = sorted({cfg['crush'] for cfg in VEG_OILS.values()})
    df = df[(df['GEO'] == 'Canada') & df['Process'].isin(CRUSH_PROCESS)
            & df['Commodity'].isin(crushers)].copy()
    _require_units(df, 'Tonnes', '32100352')
    df['KMT'] = pd.to_numeric(df['VALUE'], errors='coerce') * TONNES_TO_KMT
    df['Date'] = pd.to_datetime(df['REF_DATE'] + '-01')
    df['Metric'] = df['Process'].map(CRUSH_PROCESS)
    return df.set_index(['Commodity', 'Date', 'Metric'])['KMT'].unstack('Metric')


def load_feedstock() -> pd.Series:
    """25100082, Canada, 'Vegetable oils, total' / 'Inputs', KMT by month."""
    df = pd.read_csv(FEEDSTOCK_CSV, dtype=str, low_memory=False)
    df = df[(df['GEO'] == 'Canada') & (df['Products'] == 'Vegetable oils, total')
            & (df['Supply and disposition'] == 'Inputs')].copy()
    _require_units(df, 'Metric tonnes', '25100082')
    df['Date'] = pd.to_datetime(df['REF_DATE'] + '-01')
    return (pd.to_numeric(df['VALUE'], errors='coerce') * TONNES_TO_KMT).set_axis(df['Date']).dropna()


def cimt_trade(cimt_df: pd.DataFrame, registry: Dict) -> Dict[str, pd.DataFrame]:
    """{product: DataFrame[Exports, Imports] by month}. Inside the CIMT file's
    coverage a month with no rows is 0 (no trade reported); outside it, NaN."""
    out = {}
    for oil, cfg in registry.items():
        d = cimt_df[cimt_df['Commodity'] == cfg['cimt']].dropna(subset=['Date'])
        if d.empty:
            raise ValueError(f"No CIMT rows for '{cfg['cimt']}' ({oil}); check its CIMT workbook")
        t = (d.groupby([pd.to_datetime(d['Date']), 'Trade_Type'])['Value_KMT'].sum()
             .unstack().rename(columns={'Export': 'Exports', 'Import': 'Imports'}))
        idx = pd.date_range(t.index.min(), t.index.max(), freq='MS')
        out[oil] = t.reindex(idx).reindex(columns=['Exports', 'Imports']).fillna(0.0)
    return out


# ------------------------------------------------------------------- build
def _crop_year(d: pd.Timestamp, start: int) -> int:
    return d.year if d.month >= start else d.year - 1


def build_vegoil(cimt_df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (monthly, weights). monthly: one row per oil x month from the
    first 2015-16 month to the last 32100352 month; weights: the proxy split
    of the feedstock total by month."""
    starts = oil_start_months()
    crush, feed, trade = load_crush(), load_feedstock(), cimt_trade(cimt_df, VEG_OILS)

    frames = {}
    for oil, cfg in VEG_OILS.items():
        c = crush.loc[cfg['crush']]
        first = pd.Timestamp(VEG_OIL_FIRST_CROP_YEAR, starts[oil], 1)
        dates = pd.date_range(first, c.index.max(), freq='MS')
        df = pd.DataFrame(index=dates)
        df['Crush'] = c['Crush'].reindex(dates)
        df['Oil_Yield'] = c['Production'].reindex(dates) / df['Crush']
        df['Production'] = df['Crush'] * df['Oil_Yield']
        df['Surveyed Stocks'] = c['Stocks'].reindex(dates)
        df['Opening Survey'] = c['Stocks'].reindex(dates - pd.DateOffset(months=1)).values
        df[['Exports', 'Imports']] = trade[oil].reindex(dates)
        frames[oil] = df

    # Proxy weights on apparent domestic availability
    # (from the raw series, so every month of either oil's window has both oils)
    dates = pd.date_range(min(f.index.min() for f in frames.values()),
                          max(f.index.max() for f in frames.values()), freq='MS')
    avail = pd.DataFrame({
        oil: (crush.loc[cfg['crush'], 'Production'].reindex(dates)
              + trade[oil]['Imports'].reindex(dates) - trade[oil]['Exports'].reindex(dates)
              ).clip(lower=0)
        for oil, cfg in VEG_OILS.items()})
    w = avail['Canola Oil'] / avail.sum(axis=1, min_count=2)
    basis = pd.Series(np.where(w.notna(), 'apparent supply', ''), index=dates)
    carried = w.rolling(12, min_periods=1).mean().where(w.notna()).ffill()   # held flat
    gap = w.isna() & carried.notna()
    w = w.where(~gap, carried)
    basis[gap] = 'weight carried (oil suppressed)'

    total = feed.reindex(dates)
    baseline = feed[feed.index.year == FEEDSTOCK_BASELINE_YEAR].mean()
    pre = dates < feed.index.min()
    total[pre] = baseline
    feed_basis = pd.Series(np.where(pre, f'flatlined {FEEDSTOCK_BASELINE_YEAR} avg',
                                    np.where(total.notna(), '25100082 Inputs', '')), index=dates)
    weights = pd.DataFrame({'Feedstock_Total': total, 'Weight_Canola Oil': w,
                            'Weight_Soy Oil': 1 - w, 'Weight_Basis': basis,
                            'Feedstock_Basis': feed_basis})

    rows = []
    for oil, df in frames.items():
        df['Feedstock_Total'] = total.reindex(df.index)
        df['Weight'] = weights[f'Weight_{oil}'].reindex(df.index)
        df['Industrial'] = df['Feedstock_Total'] * df['Weight']
        df['Industrial_Basis'] = (feed_basis.reindex(df.index) + ' | '
                                  + basis.reindex(df.index))
        _residual_plug(df, 'Food', ['Industrial'])
        df['Oil'] = oil
        df['Crop_Year'] = [_crop_year(d, starts[oil]) for d in df.index]
        df['CY_Month'] = [(d.month - starts[oil]) % 12 + 1 for d in df.index]
        rows.append(df)

    monthly = pd.concat(rows).rename_axis('Date').reset_index()
    monthly['CHECK'] = (monthly['Beginning Stocks'] + monthly['Production'] + monthly['Imports']
                        - monthly['Exports'] - monthly['Industrial'] - monthly['Food']
                        - monthly['Ending Stocks'])
    cols = ['Date', 'Oil', 'Crop_Year', 'CY_Month', 'Crush', 'Oil_Yield', 'Beginning Stocks',
            'Production', 'Imports', 'Exports', 'Feedstock_Total', 'Weight', 'Industrial',
            'Food', 'Ending Stocks', 'Is_Anchor', 'Industrial_Basis', 'CHECK']
    return monthly[cols], weights.rename_axis('Date').reset_index()


def _residual_plug(df: pd.DataFrame, plug: str, uses) -> None:
    """The balancing plug (oils: Food (Residual); meals: Feed & Residual) per
    segment between surveyed month-end stocks, spread evenly; stocks rolled
    Beg[t] = End[t-1] and snapped to each survey. `uses` = disposition lines
    other than Exports and the plug."""
    df[plug] = np.nan
    opening = df['Opening Survey'].iloc[0]
    seg = []
    for d in df.index:
        seg.append(d)
        end = df.at[d, 'Surveyed Stocks']
        if pd.isna(end):
            continue
        net = sum(df.at[s, 'Production'] + df.at[s, 'Imports'] - df.at[s, 'Exports']
                  - sum(df.at[s, u] for u in uses) for s in seg)
        if pd.notna(opening) and pd.notna(net):
            df.loc[seg, plug] = (opening + net - end) / len(seg)
        opening, seg = end, []

    beg, begs, ends = df['Opening Survey'].iloc[0], [], []
    for d in df.index:
        end = (beg + df.at[d, 'Production'] + df.at[d, 'Imports'] - df.at[d, 'Exports']
               - sum(df.at[d, u] for u in uses) - df.at[d, plug])
        if pd.notna(df.at[d, 'Surveyed Stocks']):
            end = df.at[d, 'Surveyed Stocks']            # remove float drift at surveys
        begs.append(beg)
        ends.append(end)
        beg = end
    df['Beginning Stocks'], df['Ending Stocks'] = begs, ends
    df['Is_Anchor'] = df['Surveyed Stocks'].notna()


# ------------------------------------------------------------------ annual
ANNUAL_SUM = ['Crush', 'Production', 'Imports', 'Exports', 'Industrial', 'Food']


def annual_vegoil(monthly: pd.DataFrame) -> pd.DataFrame:
    """One row per oil x complete marketing year (12 months, every flow known,
    closing stock surveyed). Incomplete years are dropped, never estimated."""
    out = []
    for (oil, cy), g in monthly.groupby(['Oil', 'Crop_Year']):
        g = g.sort_values('CY_Month')
        ok = (len(g) == 12 and cy >= VEG_OIL_FIRST_CROP_YEAR
              and g[ANNUAL_SUM + ['Beginning Stocks', 'Ending Stocks']].notna().all().all()
              and bool(g['Is_Anchor'].iloc[-1]))
        if not ok:
            continue
        r = {'Oil': oil, 'Crop_Year': int(cy), **{m: g[m].sum() for m in ANNUAL_SUM},
             'Beginning Stocks': g['Beginning Stocks'].iloc[0],
             'Ending Stocks': g['Ending Stocks'].iloc[-1]}
        r['Yield'] = r['Production'] / r['Crush']
        r['CHECK'] = (r['Beginning Stocks'] + r['Production'] + r['Imports'] - r['Exports']
                      - r['Industrial'] - r['Food'] - r['Ending Stocks'])
        out.append(r)
    return pd.DataFrame(out)


def run_vegoil(cimt_df: pd.DataFrame) -> pd.DataFrame:
    """Build, check and save data_store\\CMFT_VegOil_Master.csv."""
    monthly, weights = build_vegoil(cimt_df)
    annual = annual_vegoil(monthly)

    worst = monthly['CHECK'].abs().max()
    worst_yr = annual['CHECK'].abs().max()
    print(f"    Max CHECK residual: monthly {worst:.2e} KMT, annual {worst_yr:.2e} KMT "
          f"(tolerance {TOLERANCE:.0e})")
    if not (worst <= TOLERANCE and worst_yr <= TOLERANCE):
        raise ValueError(f"Vegetable oil balance exceeds {TOLERANCE} KMT (monthly {worst}, annual {worst_yr})")
    for oil, g in annual.groupby('Oil'):
        yrs = sorted(g['Crop_Year'])
        last_cy = int(monthly.loc[monthly['Oil'] == oil, 'Crop_Year'].max())
        missing = [crop_year_label(y) for y in range(VEG_OIL_FIRST_CROP_YEAR, last_cy + 1)
                   if y not in yrs]
        print(f"    {oil:<10} complete years {crop_year_label(yrs[0])} to {crop_year_label(yrs[-1])}"
              f" ({len(yrs)})" + (f"; incomplete: {missing}" if missing else ""))
        neg = monthly[(monthly['Oil'] == oil) & (monthly['Food'] < 0)]
        if len(neg):
            print(f"      NOTE: Food (Residual) < 0 in {len(neg)} months "
                  f"(min {neg['Food'].min():.1f} KMT, {neg.loc[neg['Food'].idxmin(), 'Date']:%Y-%m})")
    flagged = monthly[monthly['Industrial_Basis'].str.contains('carried')]
    if len(flagged):
        print(f"    Proxy weight carried forward (StatCan suppressed crush): "
              f"{flagged['Date'].min():%Y-%m} to {flagged['Date'].max():%Y-%m}")

    DATA_STORE_DIR.mkdir(parents=True, exist_ok=True)
    monthly.to_csv(VEGOIL_FACT_TABLE, index=False)
    print(f"    [OK] Written {len(monthly)} rows to {VEGOIL_FACT_TABLE.name}")
    return monthly
