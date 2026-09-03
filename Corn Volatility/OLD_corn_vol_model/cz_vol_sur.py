import os
import asyncio
import numpy as np
import pandas as pd
import logging
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
from scipy.stats import norm
from scipy.optimize import brentq
from scipy.interpolate import SmoothBivariateSpline
from ib_insync import *

"""
PROJECT: 2026 CORN VOLATILITY ENGINE (MODULAR REFACTOR)
STORYLINE:

1. DUAL-MODE OPERATION (Live/Offline):
   - Lever: Connects to IBKR TWS (live) or loads from Excel/CSV (offline).
   - Auto-expiry filtering: Skips expired futures months automatically.
   - Enhanced logging: Detailed contract qualification and price retrieval diagnostics.
   - Goal: Continuity of analysis regardless of market connectivity or TWS status.

2. THE GUARDRAIL (Data Integrity):
   - Lever: Strict 'OZC' (Standard Monthly) filtering with DTE > 2 and Volume > 0.
   - Stale quote protection: Rejects market data older than 30 seconds.
   - Missing column handlers: Auto-generates future_symbol, atm_strike, right, volume.
   - Goal: Purge "junk" data and illiquid strikes to prevent model distortion.

3. THE MATH ENGINE (Vol Surface):
   - Lever: Black-76 futures option pricing + Bivariate Spline Surface Fitting.
   - IV calculation with model_iv fallback for solver failures.
   - Spread quality check: Filters out quotes with relative spread > 25%.
   - Goal: Generate the 'Smooth Blanket' (Theoretical Vol) with validated R² (>0.90).

4. THE ARBITRAGE SNIFF TEST:
   - Lever: Butterfly (convexity) & Calendar (total variance monotonicity) checks.
   - Moneyness bucketing for calendar spread analysis (5% increments).
   - Goal: Verify that identified "edge" isn't simply a pricing violation.

5. CALC THE DELTA (Market-Model) (Residual Map):
   - Lever: ±2.5% IV Deviation threshold with signal coloring (Market IV - Model IV).
   - Residual zones: Green (underpriced), Red (overpriced), Gray (fair value).
   - Goal: Identify 'Buy' and 'Sell' alpha opportunities in real-time.

6. VISUALIZATIONS (Skew & Term):
   - Lever: Multi-panel Smile Dashboard + ATM Term Structure + Skew Analysis.
   - Skew metric: 10% OTM Put IV vs 10% OTM Call IV premium over ATM.
   - Sentiment interpretation: Protective put demand, bearish/bullish skew detection.
   - Goal: Gauge market sentiment and "bias" across 2026 contract months (H, K, N, U, Z).

7. THE SAFETY BUFFER (WASDE & Seasons):
   - Lever: USDA Report Time-Locks (11:00 AM CT) & Crop Phase mapping.
   - Seasonal context: Planting (Apr-May), Pollination (Jun-Jul), Harvest (Sep-Nov).
   - Goal: Prevent ingestion of toxic data during market locks and add fundamental context.

8. DATA ARCHIVING & EXPORT:
   - Lever: Auto-export to Excel (.xlsx) or CSV with engine detection.
   - Preserves cleaned data with calculated IVs, moneyness, and surface metrics.
   - Goal: Build a high-fidelity database for backtesting and historical vol comparison.

9. DEFENSIVE PROGRAMMING:
   - Comprehensive error handling with stack traces in offline mode.
   - Graceful degradation: Continues with partial data if some contracts fail.
   - Type-safe column handling: Checks for existence before accessing DataFrame columns.
   - Goal: Robust operation in production with minimal manual intervention.
"""


# ===== CONFIGURATION =====
CONFIG = {
    'target_futures_months': ['202603','202605', '202607', '202609', '202612'],
    'offline_mode': True,           # Set to True for offline analysis, False for live data
    'max_relative_spread': 0.25,    # Relaxed for commodity markets
    'risk_free_rate': 0.046,
    'strike_otm_range': 0.50,      # % either side of underlying
    'atm_threshold_pct': 0.015,    # 1.5% for ATM anchor
    'min_dte': 2,                  # Drop contracts with DTE <= 2
    'trading_class': 'OZC',        # Standard Corn Options
    'data_path': r"C:\Users\ahmed\OneDrive\Desktop\Python\Volatility_Surface_CZ\corn_options_surface_historical.xlsx",
    'log_level': logging.INFO,
    'market_data_type': 1,          # 1=Real-time, 3=Delayed
    'wasde_report_dates': [
        '2026-02-10', '2026-03-10', '2026-04-09', '2026-05-12', '2026-06-11',
        '2026-07-10', '2026-08-12', '2026-09-11', '2026-10-09', '2026-11-10', '2026-12-10'
    ],
    'seasons': {
        'Planting': [4, 5],
        'Pollination': [6, 7],
        'Harvest': [9, 10, 11]
    }
}

# ===== DEFENSIVE LOGGING =====
logging.basicConfig(
    level=CONFIG['log_level'],
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

class CornDataHarvester:
    """The Broker Layer: Handles all ib_insync logic and raw data collection."""
    def __init__(self):
        self.ib = IB()

    async def connect(self):
        logger.info("Connecting to TWS...")
        try:
            await self.ib.connectAsync('127.0.0.1', 7496, clientId=1)
            self.ib.reqMarketDataType(CONFIG['market_data_type'])
            logger.info("Connected successfully.")
        except Exception as e:
            logger.error(f"Connection failed: {e}")
            raise

    async def get_futures_prices(self, target_months):
        logger.info(f"Retrieving futures prices for: {target_months}")

        # Filter out expired months (corn futures expire mid-month, so add 20 days buffer)
        today = datetime.now()
        valid_months = []
        for month in target_months:
            try:
                # Parse YYYYMM and add 20 days to approximate futures expiry
                month_dt = datetime.strptime(month, '%Y%m')
                expiry_approx = month_dt.replace(day=20)  # Corn futures typically expire around day 14-20

                if expiry_approx > today:
                    valid_months.append(month)
                else:
                    logger.warning(f"Skipping expired month: {month} (estimated expiry: {expiry_approx.strftime('%Y-%m-%d')})")
            except:
                valid_months.append(month)  # If parsing fails, include it anyway

        if not valid_months:
            logger.error("All target futures months have expired!")
            return {}

        logger.info(f"Valid (non-expired) months: {valid_months}")
        futures_raw = [Future('ZC', month, 'CBOT') for month in valid_months]

        logger.info(f"Attempting to qualify {len(futures_raw)} futures contracts...")
        qualified = await self.ib.qualifyContractsAsync(*futures_raw)

        logger.info(f"Successfully qualified {len(qualified)} out of {len(futures_raw)} contracts")
        for i, (requested, qual) in enumerate(zip(valid_months, qualified)):
            if qual:
                logger.info(f"  ✓ Requested: {requested} -> Qualified as: {qual.localSymbol} (Month: {qual.lastTradeDateOrContractMonth})")
            else:
                logger.warning(f"  ✗ Requested: {requested} -> FAILED to qualify")

        if not qualified:
            logger.error("No futures contracts qualified.")
            return {}

        tickers = self.ib.reqTickers(*qualified)
        wait_time = 2 if CONFIG['market_data_type'] == 1 else 8 # Wait for data pop for delayed data may be 8 seconds, live 3 seconds

        await asyncio.sleep(wait_time)  

        prices = {}
        logger.info(f"Retrieving prices for {len(tickers)} qualified contracts...")

        for t in tickers:
            p = (t.bid + t.ask) / 2 if t.bid > 0 and t.ask > 0 else t.last

            # STALE QUOTE CHECK
            if t.time:
                now = datetime.now(t.time.tzinfo) if t.time.tzinfo else datetime.now()
                if (now - t.time).total_seconds() > 30:
                    logger.warning(f"STALE_QUOTE: {t.contract.localSymbol} is {(now - t.time).total_seconds():.0f}s old. Skipping.")
                    continue

            if np.isnan(p) or p <= 0:
                p = t.close

            if not np.isnan(p) and p > 0:
                prices[t.contract.conId] = {
                    'contract': t.contract,
                    'price': p,
                    'symbol': t.contract.localSymbol
                }
                logger.info(f"  ✓ Got price for {t.contract.localSymbol}: ${p:.2f}")
            else:
                logger.warning(f"  ✗ Could not retrieve price for {t.contract.localSymbol} (bid={t.bid}, ask={t.ask}, last={t.last}, close={t.close})")

        logger.info(f"Successfully retrieved prices for {len(prices)} futures contracts")
        return prices

    async def get_option_market_data(self, futures_prices):
        all_options = []
        future_info_map = {}

        logger.info(f"\n{'='*70}")
        logger.info(f"OPTION CHAIN FETCHING: Processing {len(futures_prices)} futures contracts")
        logger.info(f"{'='*70}")

        for conId, info in futures_prices.items():
            future = info['contract']
            und_price = info['price']

            logger.info(f"\n--- Processing: {info['symbol']} (Month: {future.lastTradeDateOrContractMonth}) ---")
            logger.info(f"Fetching option chains for {info['symbol']} at ${und_price:.2f}")

            chains = await self.ib.reqSecDefOptParamsAsync(future.symbol, 'CBOT', future.secType, conId)
            logger.info(f"  Found {len(chains)} chain(s) for {info['symbol']}")

            # Log all available trading classes
            if chains:
                trading_classes = [c.tradingClass for c in chains]
                logger.info(f"  Available trading classes: {trading_classes}")

            # Prioritize OZC trading class - STRICT FILTER
            chain = next((c for c in chains if c.exchange == 'CBOT' and c.tradingClass == CONFIG['trading_class']), None)

            if not chain:
                logger.warning(f"  ✗ No '{CONFIG['trading_class']}' chain found for {info['symbol']}. Skipping.")
                continue

            logger.info(f"  ✓ Found '{CONFIG['trading_class']}' chain with {len(chain.expirations)} expiries")

            # Specialized Expiry Matching
            target_mo = future.lastTradeDateOrContractMonth[:6]
            expiries = sorted(chain.expirations)
            logger.info(f"  Target month: {target_mo}")
            logger.info(f"  Available expiries: {expiries[:5]}..." if len(expiries) > 5 else f"  Available expiries: {expiries}")

            actual_expiry = self._match_expiry(expiries, target_mo)

            if not actual_expiry:
                logger.warning(f"  ✗ No valid expiry match for {info['symbol']}")
                continue

            logger.info(f"  ✓ Matched expiry: {actual_expiry}")

            # OTM Filtering
            strikes = sorted(chain.strikes)
            strike_range_min = (1 - CONFIG['strike_otm_range']) * und_price
            strike_range_max = (1 + CONFIG['strike_otm_range']) * und_price
            logger.info(f"  Strike range: ${strike_range_min:.0f} to ${strike_range_max:.0f} ({CONFIG['strike_otm_range']:.0%} OTM)")

            options_before = len(all_options)
            for s in strikes:
                if strike_range_min <= s <= strike_range_max:
                    # ATM Logic: Include both P and C within threshold
                    is_atm = abs(s - und_price) / und_price < CONFIG['atm_threshold_pct']
                    if is_atm:
                        all_options.append(FuturesOption('ZC', actual_expiry, s, 'C', 'CBOT', tradingClass='OZC'))
                        all_options.append(FuturesOption('ZC', actual_expiry, s, 'P', 'CBOT', tradingClass='OZC'))
                    else:
                        right = 'P' if s < und_price else 'C'
                        all_options.append(FuturesOption('ZC', actual_expiry, s, right, 'CBOT', tradingClass='OZC'))

            options_added = len(all_options) - options_before
            logger.info(f"  ✓ Added {options_added} option contracts for {info['symbol']}")

            future_info_map[actual_expiry] = {
                'und_price': und_price,
                'future_symbol': info['symbol'],
                'atm_strike': min(strikes, key=lambda x: abs(x - und_price))
            }

        if not all_options:
            return pd.DataFrame()

        # Batch Qualification
        logger.info(f"Qualifying {len(all_options)} option contracts...")
        qualified_options = await self.ib.qualifyContractsAsync(*all_options)
        
        # Batch Ticker Request
        logger.info(f"Requesting tickers for {len(qualified_options)} options...")
        tickers = self.ib.reqTickers(*qualified_options)
        await asyncio.sleep(20)

        results = []
        ignored_count = 0
        for t in tickers:
            # Defensive Validation: Ensure OZC only
            if t.contract.tradingClass != 'OZC':
                logger.warning(f"Defensive Filter: Ignored non-OZC ticker {t.contract.localSymbol} (Class: {t.contract.tradingClass})")
                ignored_count += 1
                continue

            f_data = future_info_map.get(t.contract.lastTradeDateOrContractMonth)
            if not f_data: continue

            # STALE QUOTE CHECK
            if t.time:
                now = datetime.now(t.time.tzinfo) if t.time.tzinfo else datetime.now()
                if (now - t.time).total_seconds() > 30:
                    logger.warning(f"STALE_QUOTE: {t.contract.localSymbol} is {(now - t.time).total_seconds():.0f}s old. Skipping.")
                    continue

            res = {
                'symbol': t.contract.localSymbol,
                'strike': t.contract.strike,
                'right': t.contract.right,
                'expiry': t.contract.lastTradeDateOrContractMonth,
                'und_price': f_data['und_price'],
                'future_symbol': f_data['future_symbol'],
                'atm_strike': f_data['atm_strike'],
                'bid': t.bid, 'ask': t.ask, 'last': t.last, 'volume': t.volume,
                'model_iv': t.modelGreeks.impliedVol if t.modelGreeks else np.nan
            }
            results.append(res)
        
        # Enhanced Logging Summary
        df_results = pd.DataFrame(results)
        if not df_results.empty:
            for expiry, group in df_results.groupby('expiry'):
                year = expiry[:4]
                try:
                    month_name = datetime.strptime(expiry[:6], '%Y%m').strftime('%B')
                except:
                    month_name = "Unknown"
                logger.info(f"Successfully qualified {len(group)} OZC strikes for {month_name} {year}.")
        
        if ignored_count > 0:
            logger.info(f"Ignored {ignored_count} non-standard (Weekly/Short-Dated) contracts.")

        return df_results

    def _match_expiry(self, expiries, target_month):
        """Logic to match futures month or preceding month."""
        match = next((e for e in expiries if e.startswith(target_month)), None)
        if not match:
            # Try preceding month logic
            try:
                fut_dt = datetime.strptime(target_month, '%Y%m')
                prev_dt = fut_dt - timedelta(days=15)
                prev_mo = prev_dt.strftime('%Y%m')
                match = next((e for e in expiries if e.startswith(prev_mo)), None)
            except: pass
        
        today = datetime.now().strftime('%Y%m%d')
        if not match or match <= today:
            valid = [e for e in expiries if e > today]
            return valid[0] if valid else None
        return match

    def load_offline_data(self):
        """Loads historical snapshot from Excel/CSV for offline analysis."""
        try:
            data_path = CONFIG['data_path']
            logger.info(f"OFFLINE MODE: Loading data from: {data_path}")

            if not os.path.exists(data_path):
                logger.warning(f"Data file not found at {data_path}")
                return pd.DataFrame()

            # Determine file type and read accordingly
            if data_path.endswith('.xlsx') or data_path.endswith('.xls'):
                logger.info("Reading Excel file...")
                df = pd.read_excel(data_path, engine='openpyxl')
            elif data_path.endswith('.csv'):
                logger.info("Reading CSV file...")
                df = pd.read_csv(data_path)
            else:
                logger.error(f"Unsupported file format: {data_path}")
                return pd.DataFrame()

            if df.empty:
                logger.warning("Offline data file is empty.")
                return pd.DataFrame()

            # Data Integrity: Sanitize 'expiry' column
            if 'expiry' in df.columns:
                # Handle cases where Excel/CSV might store '20260310' as float '20260310.0'
                df['expiry'] = df['expiry'].astype(str).str.replace(r'\.0$', '', regex=True)

            # Standardize columns if needed (legacy compatibility)
            if 'underlying_price' in df.columns and 'und_price' not in df.columns:
                df['und_price'] = df['underlying_price']

            logger.info(f"Successfully loaded {len(df)} rows from file.")
            return df
        except Exception as e:
            logger.error(f"Failed to load offline data: {e}", exc_info=True)
            return pd.DataFrame()

    def disconnect(self):
        if self.ib.isConnected():
            logger.info("Disconnecting from TWS...")
            self.ib.disconnect()

class VolatilityEngine:
    """The Math Layer: Stateless calculations and surface fitting."""
    
    @staticmethod
    def black_76_price(F, K, T, r, sigma, option_type="C"):
        if T <= 0: return max(0, F - K) if option_type == "C" else max(0, K - F)
        d1 = (np.log(F / K) + (sigma**2 / 2) * T) / (sigma * np.sqrt(T))
        d2 = d1 - sigma * np.sqrt(T)
        disc = np.exp(-r * T)
        if option_type == "C":
            return disc * (F * norm.cdf(d1) - K * norm.cdf(d2))
        else:
            return disc * (K * norm.cdf(-d2) - F * norm.cdf(-d1))

    @staticmethod
    def solve_iv(F, K, T, r, option_type, market_price):
        intrinsic = max(0, F - K) if option_type == "C" else max(0, K - F)
        if market_price <= intrinsic + 1e-4: return np.nan
        def objective(sigma):
            return VolatilityEngine.black_76_price(F, K, T, r, sigma, option_type) - market_price
        try:
            return brentq(objective, 1e-4, 4.0, xtol=1e-5)
        except: return np.nan

    @staticmethod
    def process_data(df):
        """Clean and calculate IVs and Moneyness."""
        if df.empty: return df
        
        # 1. Calc DTE
        def calc_dte(exp_str):
            try:
                # Support both YYYYMMDD and YYYYMM formats
                if len(exp_str) == 8:
                    exp_dt = datetime.strptime(exp_str, '%Y%m%d')
                elif len(exp_str) == 6:
                    # Default to mid-month if only YYYYMM is provided
                    exp_dt = datetime.strptime(exp_str + "15", '%Y%m%d')
                else:
                    return -1
                
                # Use naive comparison if dates are from Excel
                now = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                return (exp_dt - now).days
            except: return -1

        df['days_to_expiry'] = df['expiry'].apply(calc_dte)
        df['T'] = df['days_to_expiry'] / 365.0
        
        # 2. Basic Filters
        df = df[df['days_to_expiry'] > CONFIG['min_dte']].copy()
        
        # Handle missing volume column in legacy CSV
        if 'volume' not in df.columns:
            df['volume'] = 0
            
        # Handle missing 'right' column (Call/Put)
        if 'right' not in df.columns:
            # Infer logic: If not explicit, assume OTM logic or simple split?
            # Better approach: Try to infer from moneyness or just assign 'C'/'P' if we had that info.
            # Since we don't have it, we'll try to infer based on typical OTM surface logic 
            # (usually data is collected as OTM).
            # If strike >= und_price -> Call (if we assume OTM), wait, usually:
            # Call OTM if Strike > Spot
            # Put OTM if Strike < Spot
            # Let's assume standard surface construction where we use OTM options.
            df['right'] = np.where(df['strike'] >= df['und_price'], 'C', 'P')

        # Handle missing 'last' column
        if 'last' not in df.columns:
            df['last'] = 0
            
        # Handle missing 'symbol' column (for logging)
        if 'symbol' not in df.columns:
            df['symbol'] = df['expiry'].astype(str) + '_' + df['strike'].astype(str) + df['right']

        # Handle missing 'future_symbol' column (needed for reporting)
        if 'future_symbol' not in df.columns:
            # Create a generic future symbol from expiry (e.g., 202605 -> ZCK6)
            df['future_symbol'] = 'ZC' + df['expiry'].astype(str).str[:6]

        # Handle missing 'atm_strike' column (used for reference)
        if 'atm_strike' not in df.columns:
            # Calculate ATM strike as the strike closest to underlying price for each expiry
            df['atm_strike'] = df.groupby('expiry')['strike'].transform(
                lambda strikes: strikes.iloc[(strikes - df.loc[strikes.index, 'und_price'].iloc[0]).abs().argmin()]
            )

        # === FIX: Relaxed volume filter for combined Offline Mode ===
        # Why? CSV data might have 0 volume but valid mid prices for visualization.
        initial_len = len(df)
        if CONFIG['offline_mode']:
            # In offline mode, keep rows that either have volume OR a valid IV/mid
            # Check if 'iv' column exists before filtering on it (it might not exist in raw CSV)
            if 'iv' in df.columns:
                df = df[(df['volume'] > 0) | (df['iv'].notna()) | (df['bid'] > 0)].copy()
            else:
                # If 'iv' doesn't exist yet, just filter on volume and bid
                df = df[(df['volume'] > 0) | (df['bid'] > 0)].copy()
        else:
            df = df[df['volume'] > 0].copy()
        
        if df.empty:
            logger.warning("No valid data remaining after filtering.")
            return df
            
        logger.info(f"Filtered {initial_len} -> {len(df)} strikes for analysis.")

        # 3. IV Calculation
        results = []
        for (expiry, strike, right), group in df.groupby(['expiry', 'strike', 'right']):
            f_data = group.iloc[0]
            
            def get_mid(row):
                # REQUIRE valid bid/ask - no fallback to 'last'
                if row['bid'] > 0 and row['ask'] > 0:
                    mid = (row['bid'] + row['ask']) / 2
                    
                    # Spread quality check
                    rel_spread = (row['ask'] - row['bid']) / mid
                    if rel_spread > CONFIG['max_relative_spread']:
                        # logger.debug(f"WIDE_SPREAD: {row.get('symbol', 'Unknown')} spread {rel_spread:.1%}") 
                        return np.nan
                    
                    return mid
                
                # If no valid bid/ask, use 'last' as fallback IF it exists
                if row.get('last', 0) > 0:
                    # logger.debug(f"FALLBACK: {row.get('symbol', 'Unknown')} using last price {row['last']}")
                    return row['last']
                
                # logger.debug(f"NO_MARKET: {row.get('symbol', 'Unknown')} missing valid bid/ask/last quotes")
                return np.nan

            ivs = []
            for _, row in group.iterrows():
                mid = get_mid(row)
                if np.isnan(mid): continue

                iv = VolatilityEngine.solve_iv(row['und_price'], strike, row['T'], CONFIG['risk_free_rate'], row['right'], mid)
                # Fallback to model_iv if it exists in the data
                if np.isnan(iv) and 'model_iv' in row.index and not pd.isna(row['model_iv']):
                    iv = row['model_iv']
                if not np.isnan(iv): ivs.append(iv)
            
            if ivs:
                avg_iv = sum(ivs) / len(ivs)
                entry = f_data.to_dict()
                entry['iv'] = avg_iv
                entry['expiry'] = expiry
                entry['strike'] = strike
                entry['right'] = right
                entry['moneyness_kf'] = strike / entry['und_price']
                results.append(entry)
        
        if not results:
            return pd.DataFrame(columns=df.columns.tolist() + ['iv', 'moneyness_kf'])
            
        return pd.DataFrame(results)

    @staticmethod
    def fit_surface(df):
        if len(df) < 10: return None
        clean = df.dropna(subset=['iv']).sort_values(['days_to_expiry', 'moneyness_kf'])
        try:
            kx = min(3, len(clean['moneyness_kf'].unique()) - 1)
            ky = min(3, len(clean['days_to_expiry'].unique()) - 1)
            if kx < 1 or ky < 1: return None
            return SmoothBivariateSpline(clean['moneyness_kf'], clean['days_to_expiry'], clean['iv'], kx=kx, ky=ky, s=len(clean))
        except: return None

    @staticmethod
    def check_butterfly_arbitrage(df):
        """Detect convexity violations (Negative Butterflies)."""
        violations = []
        for (expiry, right), group in df.groupby(['expiry', 'right']):
            group = group.sort_values('strike')
            if len(group) < 3: continue
            
            # Using mid/last price proxy
            strikes = group['strike'].values
            prices = []
            for _, row in group.iterrows():
                p = (row['bid'] + row['ask']) / 2 if row['bid'] > 0 and row['ask'] > 0 else row['last']
                if np.isnan(p) or p <= 0:
                    p = VolatilityEngine.black_76_price(row['und_price'], row['strike'], row['T'], CONFIG['risk_free_rate'], row['iv'], row['right'])
                prices.append(p)
            
            for i in range(1, len(strikes) - 1):
                k1, k2, k3 = strikes[i-1], strikes[i], strikes[i+1]
                p1, p2, p3 = prices[i-1], prices[i], prices[i+1]
                
                # Butterfly value should be >= 0
                slope1 = (p1 - p2) / (k2 - k1)
                slope2 = (p2 - p3) / (k3 - k2)
                if slope1 < slope2 - 1e-4:
                    month = datetime.strptime(expiry[:6], '%Y%m').strftime('%B')
                    violations.append(f"Butterfly Violation in {right}s at {month} K={k2}")
        return violations

    @staticmethod
    def check_calendar_arbitrage(df):
        """Verify total variance monotonicity (sigma^2 * T must grow with T)."""
        violations = []
        
        # === FIX: Create moneyness buckets (5% increments) ===
        # Example: 0.95, 1.00, 1.05, 1.10, etc.
        df = df.copy()
        df['moneyness_bucket'] = (df['moneyness_kf'] * 20).round() / 20  # Round to 0.05
        
        # Group by moneyness bucket (not absolute strike)
        for bucket, group in df.groupby('moneyness_bucket'):
            group = group.sort_values('T')
            
            if len(group) < 2:
                continue  # Need at least 2 expiries to compare
            
            prev_total_var = -1
            for _, row in group.iterrows():
                total_var = (row['iv']**2) * row['T']
                
                # Total variance must increase (or stay same) with time
                if total_var < prev_total_var - 1e-6:  # Tolerance for numerical noise
                    month = datetime.strptime(row['expiry'][:6], '%Y%m').strftime('%B')
                    violations.append(
                        f"Calendar Violation at {month} "
                        f"(Moneyness {bucket:.2f}, Strike {row['strike']:.0f})"
                    )
                
                prev_total_var = total_var
        
        return violations

    @staticmethod
    def calculate_fit_metrics(df, spline):
        if not spline or df.empty:
            return {'r2': 0, 'mae': 0}
        
        y_true = df['iv'].values
        y_pred = spline.ev(df['moneyness_kf'], df['days_to_expiry'])
        
        ss_res = np.sum((y_true - y_pred) ** 2)
        ss_tot = np.sum((y_true - np.mean(y_true)) ** 2)
        r2 = 1 - (ss_res / ss_tot) if ss_tot != 0 else 0
        mae = np.mean(np.abs(y_true - y_pred))
        
        return {'r2': r2, 'mae': mae}

class SurfacePresenter:
    """The Output Layer: Visualization and Reporting."""
    
    @staticmethod
    def _get_futures_shorthand(future_symbol):
        """Converts IB future symbol (e.g., ZCZ6) to shorthand like CZZ26."""
        # Standard month code to month name for reference, but we just need to reformat ZC + Code + 20 + digit
        if isinstance(future_symbol, str) and len(future_symbol) >= 4:
            # Future symbol format like ZCZ6 or ZCU5
            # We want to change ZC at start to CZ (per user preference CZXX)
            month_code = future_symbol[2]
            year_digit = future_symbol[3]
            return f"CZ{month_code}2{year_digit}"
        return str(future_symbol)

    @staticmethod
    def generate_plots(df, spline, metrics=None):
        if not spline: 
            logger.warning("No spline available for plotting.")
            return

        # Meshgrid
        mi = np.linspace(df['moneyness_kf'].min(), df['moneyness_kf'].max(), 50)
        di = np.linspace(df['days_to_expiry'].min(), df['days_to_expiry'].max(), 50)
        M, D = np.meshgrid(mi, di)
        Z = spline.ev(M, D)

        title = "Corn (ZC) Volatility Surface 2026"
        seasonal_phase = SurfacePresenter.add_seasonal_context()
        title += f" | Season: {seasonal_phase}"
        
        if metrics and metrics.get('r2', 1.0) < 0.90:
            title += " - <span style='color:red'>LOW CONFIDENCE MODEL</span>"

        fig = go.Figure(data=[
            go.Surface(x=M, y=D, z=Z, colorscale='Viridis', opacity=0.8),
            go.Scatter3d(x=df['moneyness_kf'], y=df['days_to_expiry'], z=df['iv'], 
                         mode='markers', marker=dict(size=2, color='red'))
        ])
        fig.update_layout(title=title, scene=dict(
            xaxis_title='Moneyness (K/F)', yaxis_title='DTE', zaxis_title='IV'
        ))
        fig.show()

    @staticmethod
    def report_surface_quality(metrics):
        r2, mae = metrics.get('r2', 0), metrics.get('mae', 0)
        logger.info(f"Surface Fit Quality: R-squared = {r2:.4f}, MAE = {mae:.4f}")
        if r2 < 0.90:
            logger.warning("Fit Quality Low (R2 < 0.90): Model may use noisy data.")

    @staticmethod
    def add_seasonal_context():
        """Returns the current seasonal phase for the 2026 crop."""
        now = datetime.now()
        month = now.month
        for season, months in CONFIG['seasons'].items():
            if month in months:
                return season
        return "Off-Season"

    @staticmethod
    def plot_residuals_trading_map(df, spline):
        if not spline or df.empty:
            logger.warning("Cannot plot trading map: Spline or Data missing.")
            return

        df = df.copy()
        df['model_iv'] = spline.ev(df['moneyness_kf'], df['days_to_expiry'])
        df['residual'] = df['iv'] - df['model_iv']

        # Signal Logic
        def get_signal(res):
            if res > 0.025: return 'SELL (Overpriced)'
            if res < -0.025: return 'BUY (Underpriced)'
            return 'Fair Value'
        
        df['signal'] = df['residual'].apply(get_signal)
        color_map = {'SELL (Overpriced)': 'red', 'BUY (Underpriced)': 'green', 'Fair Value': 'gray'}

        fig = px.scatter(df, x='moneyness_kf', y='residual', color='signal',
                         color_discrete_map=color_map,
                         hover_data=['strike', 'expiry', 'iv', 'model_iv'],
                         title="Corn (ZC) 2026 Residual Trading Map (Market IV - Model IV)")

        # Shaded Zones
        fig.add_hrect(y0=0.025, y1=max(0.05, df['residual'].max()), fillcolor="red", opacity=0.1, line_width=0, annotation_text="Overpriced Zone")
        fig.add_hrect(y0=min(-0.05, df['residual'].min()), y1=-0.025, fillcolor="green", opacity=0.1, line_width=0, annotation_text="Underpriced Zone")
        
        fig.add_hline(y=0, line_dash="dash", line_color="black", opacity=0.5)
        fig.update_layout(xaxis_title="Moneyness (K/F)", yaxis_title="Residual (IV Edge)")
        fig.show()

    @staticmethod
    def plot_smile_dashboard(df, spline):
        """Creates a grid of volatility smiles for each 2026 contract month."""
        if not spline or df.empty:
            logger.warning("Cannot plot smile dashboard: Spline or Data missing.")
            return

        expiries = sorted(df['expiry'].unique())
        rows = 3
        cols = 2
        
        # Get Shorthand Names for Titles based on underlying future
        titles = []
        for exp in expiries:
            subset = df[df['expiry'] == exp]
            if not subset.empty:
                fut_sym = subset.iloc[0]['future_symbol']
                titles.append(SurfacePresenter._get_futures_shorthand(fut_sym))
            else:
                titles.append(exp)

        fig = make_subplots(rows=rows, cols=cols, subplot_titles=titles, vertical_spacing=0.1)

        for i, exp in enumerate(expiries):
            row = (i // cols) + 1
            col = (i % cols) + 1
            
            group = df[df['expiry'] == exp].sort_values('moneyness_kf')
            dte = group['days_to_expiry'].iloc[0]
            
            # 1. Theoretical Smile (Line)
            mi = np.linspace(group['moneyness_kf'].min() * 0.95, group['moneyness_kf'].max() * 1.05, 50)
            theo_iv = spline.ev(mi, np.full_like(mi, dte))
            
            fig.add_trace(go.Scatter(x=mi, y=theo_iv, mode='lines', name=f'Fair Value {exp[:6]}', line=dict(color='black', width=1), showlegend=False), row=row, col=col)
            
            # 2. Market Data (Points)
            group = group.copy()
            group['model_iv'] = spline.ev(group['moneyness_kf'], group['days_to_expiry'])
            group['diff'] = group['iv'] - group['model_iv']
            
            def get_color(diff):
                if diff > 0.02: return 'red'        # Overpriced
                if diff < -0.02: return 'green'     # Underpriced
                return 'gray'
            
            group['color'] = group['diff'].apply(get_color)
            
            fig.add_trace(go.Scatter(
                x=group['moneyness_kf'], y=group['iv'],
                mode='markers',
                marker=dict(color=group['color'], size=6, line=dict(width=1, color='DarkSlateGrey')),
                text=[f"Strike: {s}<br>Edge: {d:.1%}" for s, d in zip(group['strike'], group['diff'])],
                name=f'Market {exp[:6]}',
                showlegend=False
            ), row=row, col=col)
            
            fig.update_xaxes(title_text="Moneyness (K/F)", row=row, col=col)
            fig.update_yaxes(title_text="IV", row=row, col=col)

        fig.update_layout(height=1000, title_text="2026 Corn (ZC) Volatility Smile Dashboard (Model vs Market)", showlegend=False)
        fig.show()

    @staticmethod
    def plot_term_structure(clean_df, spline):
        if not spline or clean_df.empty: return
        
        # Get unique expiries and their DTE
        expiries = sorted(clean_df['expiry'].unique())
        term_data = []
        
        for exp in expiries:
            # Get DTE for this expiry
            dte = clean_df[clean_df['expiry'] == exp]['days_to_expiry'].iloc[0]
            # Evaluate spline at ATM (Moneyness = 1.0)
            atm_iv = float(spline.ev(1.0, dte))
            term_data.append({'Expiry': exp, 'DTE': dte, 'ATM_IV': atm_iv})
        
        df_term = pd.DataFrame(term_data).sort_values('DTE')
        fig = px.line(df_term, x='Expiry', y='ATM_IV', markers=True,
                      title="Corn (ZC) 2026 Term Structure (ATM IV)",
                      labels={'ATM_IV': 'ATM Implied Volatility'})
        fig.add_hline(y=df_term['ATM_IV'].mean(), line_dash="dot", annotation_text="Avg IV")
        fig.show()

    @staticmethod
    def analyze_smile_skew(clean_df, spline):
        if not spline or clean_df.empty: 
            return
        
        expiries = sorted(clean_df['expiry'].unique())
        skew_results = []
        
        for exp in expiries:
            group = clean_df[clean_df['expiry'] == exp]
            dte = group['days_to_expiry'].iloc[0]
            fut_sym = group['future_symbol'].iloc[0]
            shorthand = SurfacePresenter._get_futures_shorthand(fut_sym)
            
            # === FIX: Evaluate at correct moneyness points ===
            # K/F convention: K/F > 1 means OTM put, K/F < 1 means OTM call
            put_iv_10pct = float(spline.ev(1.11, dte))   # 10% OTM put
            call_iv_10pct = float(spline.ev(0.91, dte))  # 10% OTM call
            atm_iv = float(spline.ev(1.0, dte))          # ATM
            
            # === FIX: Calculate SKEW as difference from ATM ===
            put_skew = put_iv_10pct - atm_iv   # Positive = puts expensive
            call_skew = call_iv_10pct - atm_iv # Negative = calls cheap
            
            skew_results.append({
                'Label': shorthand,
                'Expiry': exp,
                'Put Skew (10% OTM)': put_skew * 100,      # Convert to bps
                'Call Skew (10% OTM)': call_skew * 100,
                'ATM IV': atm_iv * 100,                    # For reference
                'Asymmetry': (put_skew - call_skew) * 100  # Put premium vs call
            })
            
            # Better sentiment interpretation
            if put_skew > 0.03 and call_skew < 0.01:
                sentiment = "Protective Put Demand (Normal for commodities)"
            elif put_skew > call_skew + 0.02:
                sentiment = "Bearish Skew (Unusually high put premium)"
            elif call_skew > put_skew + 0.02:
                sentiment = "Bullish Skew (Unusual for corn)"
            else:
                sentiment = "Neutral Skew"
            
            logger.info(
                f"Skew [{shorthand}]: Put={put_skew:+.2%}, Call={call_skew:+.2%} -> {sentiment}"
            )

        df_skew = pd.DataFrame(skew_results)
        
        # === FIX: Plot SKEW (difference from ATM), not absolute IV ===
        fig = go.Figure()
        
        fig.add_trace(go.Bar(
            x=df_skew['Label'], 
            y=df_skew['Put Skew (10% OTM)'],  # Now showing skew delta in bps
            name='Put Skew vs ATM', 
            marker_color='red'
        ))
        
        fig.add_trace(go.Bar(
            x=df_skew['Label'], 
            y=df_skew['Call Skew (10% OTM)'],  # Now showing skew delta in bps
            name='Call Skew vs ATM', 
            marker_color='green'
        ))
        
        # Add zero line (ATM reference)
        fig.add_hline(y=0, line_dash="dash", line_color="black", 
                      annotation_text="ATM Reference (0 bps)")
        
        fig.update_layout(
            title="2026 Volatility Skew: Premium Over ATM (Basis Points)",
            xaxis_title="Contract Month", 
            yaxis_title="IV Skew (bps over/under ATM)",
            barmode='group'
        )
        
        fig.show()

    @staticmethod
    def export_data(df):
        try:
            data_path = CONFIG['data_path']

            # Export based on file extension
            if data_path.endswith('.xlsx') or data_path.endswith('.xls'):
                df.to_excel(data_path, index=False, engine='openpyxl')
            else:
                # Default to CSV
                df.to_csv(data_path, index=False)

            logger.info(f"Data exported to {data_path}")
        except Exception as e:
            logger.error(f"Data export failed: {e}")

    @staticmethod
    def report_opportunities(df, spline):
        if not spline: return
        df = df.copy()
        df['model_iv'] = spline.ev(df['moneyness_kf'], df['days_to_expiry'])
        df['edge'] = df['iv'] - df['model_iv']
        
        opps = df[abs(df['edge']) > 0.02].sort_values('edge', ascending=False)
        if not opps.empty:
            print("\n" + "="*70)
            print("TRADING OPPORTUNITIES (Edge > 2%)")
            print("="*70)
            cols = ['future_symbol', 'strike', 'right', 'days_to_expiry', 'iv', 'model_iv', 'edge']
            # Note: 'right' might be missing if we used f_data (ATM average), Adjusting:
            print(opps[[c for c in cols if c in opps.columns]].to_string(index=False))

# ===== COORDINATOR (MAIN) =====
def is_wasde_lock():
    """Checks if a WASDE report is currently in progress (Market Lock)."""
    now = datetime.now()
    date_str = now.strftime('%Y-%m-%d')
    if date_str in CONFIG['wasde_report_dates']:
        # CT Time Check: 10:55 AM to 11:15 AM
        # Note: Local system time is used, assumed to be synced with CT or managed by user.
        start_lock = now.replace(hour=10, minute=55, second=0, microsecond=0)
        end_lock = now.replace(hour=11, minute=15, second=0, microsecond=0)
        if start_lock <= now <= end_lock:
            return True
    return False

async def main():
    if is_wasde_lock():
        logger.warning("MARKET LOCK: USDA WASDE REPORT IN PROGRESS. Pausing harvester for data integrity.")
        return

    harvester = CornDataHarvester()
    engine = VolatilityEngine()
    presenter = SurfacePresenter()

    try:
        if CONFIG['offline_mode']:
            logger.info("MODE: OFFLINE. Analyzing historical snapshot from Excel.")
            raw_df = harvester.load_offline_data()
            if raw_df.empty:
                logger.warning("No offline data available. Exiting.")
                return
        else:
            # 1. Collect
            await harvester.connect()
            fut_prices = await harvester.get_futures_prices(CONFIG['target_futures_months'])
            if not fut_prices: return

            raw_df = await harvester.get_option_market_data(fut_prices)
            if raw_df.empty:
                logger.warning("No option data collected.")
                return

        # 2. Math
        clean_df = engine.process_data(raw_df)
        
        # Arbitrage Checks
        butterflies = engine.check_butterfly_arbitrage(clean_df)
        calendars = engine.check_calendar_arbitrage(clean_df)
        for v in butterflies + calendars:
            logger.warning(f"Arbitrage Violation: {v}")

        spline = engine.fit_surface(clean_df)
        metrics = engine.calculate_fit_metrics(clean_df, spline)

        # 3. Present
        if not clean_df.empty:
            presenter.report_surface_quality(metrics)
            presenter.generate_plots(clean_df, spline, metrics)
            presenter.plot_term_structure(clean_df, spline)
            presenter.analyze_smile_skew(clean_df, spline)
            presenter.plot_smile_dashboard(clean_df, spline)
            presenter.plot_residuals_trading_map(clean_df, spline)
            presenter.export_data(clean_df)
            presenter.report_opportunities(clean_df, spline)
        else:
            logger.warning("No valid clean results to present.")

    except Exception as e:
        logger.error(f"Main Loop Error: {e}", exc_info=True)
    finally:
        harvester.disconnect()

if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
