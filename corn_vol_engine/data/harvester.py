"""
data/harvester.py
==================
The Broker Layer: dual-mode data acquisition -- live IBKR TWS connectivity
(async) or offline Excel/CSV loading. Handles contract qualification,
OZC (standard monthly) filtering, stale-quote protection, and per-month
graceful degradation.
"""

import os
import logging
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from ib_insync import IB, Future, FuturesOption

from config import CONFIG

logger = logging.getLogger(__name__)


class CornDataHarvester:
    """Handles all ib_insync connectivity and raw data collection."""

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
                month_dt = datetime.strptime(month, '%Y%m')
                expiry_approx = month_dt.replace(day=20)

                if expiry_approx > today:
                    valid_months.append(month)
                else:
                    logger.warning(f"Skipping expired month: {month} (estimated expiry: {expiry_approx.strftime('%Y-%m-%d')})")
            except Exception:
                valid_months.append(month)  # If parsing fails, include it anyway

        if not valid_months:
            logger.error("All target futures months have expired!")
            return {}

        logger.info(f"Valid (non-expired) months: {valid_months}")
        futures_raw = [Future('ZC', month, 'CBOT') for month in valid_months]

        logger.info(f"Attempting to qualify {len(futures_raw)} futures contracts...")
        try:
            qualified_all = await self.ib.qualifyContractsAsync(*futures_raw)
        except Exception as e:
            logger.error(f"Batch qualification failed unexpectedly: {e}", exc_info=True)
            qualified_all = [None] * len(futures_raw)

        qualified = []
        for requested, qual in zip(valid_months, qualified_all):
            if qual:
                logger.info(f"  ✓ Requested: {requested} -> Qualified as: {qual.localSymbol} (Month: {qual.lastTradeDateOrContractMonth})")
                qualified.append(qual)
            else:
                logger.warning(f"  ✗ Requested: {requested} -> FAILED to qualify. Skipping this month, continuing with remaining contracts.")

        if not qualified:
            logger.error("No futures contracts qualified.")
            return {}

        # Async ticker gathering -- waits on the actual data event rather than
        # blindly sleeping for a fixed duration regardless of arrival time.
        try:
            tickers = await self.ib.reqTickersAsync(*qualified)
        except Exception as e:
            logger.error(f"Async ticker fetch failed: {e}", exc_info=True)
            tickers = []

        prices = {}
        logger.info(f"Retrieving prices for {len(tickers)} qualified contracts...")

        for t in tickers:
            try:
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
            except Exception as e:
                logger.error(f"Unexpected error processing ticker {getattr(t.contract, 'localSymbol', '?')}: {e}", exc_info=True)
                continue

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

            try:
                logger.info(f"\n--- Processing: {info['symbol']} (Month: {future.lastTradeDateOrContractMonth}) ---")
                logger.info(f"Fetching option chains for {info['symbol']} at ${und_price:.2f}")

                chains = await self.ib.reqSecDefOptParamsAsync(future.symbol, 'CBOT', future.secType, conId)
                logger.info(f"  Found {len(chains)} chain(s) for {info['symbol']}")

                if chains:
                    trading_classes = [c.tradingClass for c in chains]
                    logger.info(f"  Available trading classes: {trading_classes}")

                # Prioritize OZC trading class - STRICT FILTER (rejects weekly/serial noise)
                chain = next((c for c in chains if c.exchange == 'CBOT' and c.tradingClass == CONFIG['trading_class']), None)

                if not chain:
                    logger.warning(f"  ✗ No '{CONFIG['trading_class']}' chain found for {info['symbol']}. Skipping this month, continuing.")
                    continue

                logger.info(f"  ✓ Found '{CONFIG['trading_class']}' chain with {len(chain.expirations)} expiries")

                target_mo = future.lastTradeDateOrContractMonth[:6]
                expiries = sorted(chain.expirations)
                logger.info(f"  Target month: {target_mo}")
                logger.info(f"  Available expiries: {expiries[:5]}..." if len(expiries) > 5 else f"  Available expiries: {expiries}")

                actual_expiry = self._match_expiry(expiries, target_mo)

                if not actual_expiry:
                    logger.warning(f"  ✗ No valid expiry match for {info['symbol']}. Skipping this month, continuing.")
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
            except Exception as e:
                logger.error(f"  ✗ Unexpected error processing {info.get('symbol', conId)}: {e}. Skipping this month, continuing.", exc_info=True)
                continue

        if not all_options:
            return pd.DataFrame()

        logger.info(f"Qualifying {len(all_options)} option contracts...")
        try:
            qualified_options = await self.ib.qualifyContractsAsync(*all_options)
        except Exception as e:
            logger.error(f"Option qualification failed unexpectedly: {e}", exc_info=True)
            qualified_options = []

        if not qualified_options:
            logger.warning("No option contracts qualified.")
            return pd.DataFrame()

        # Async ticker gathering -- waits on the actual data event rather than
        # blindly sleeping for a fixed duration regardless of arrival time.
        logger.info(f"Requesting tickers for {len(qualified_options)} options...")
        try:
            tickers = await self.ib.reqTickersAsync(*qualified_options)
        except Exception as e:
            logger.error(f"Async option ticker fetch failed: {e}", exc_info=True)
            tickers = []

        results = []
        ignored_count = 0
        for t in tickers:
            # Defensive Validation: Ensure OZC only
            if t.contract.tradingClass != 'OZC':
                logger.warning(f"Defensive Filter: Ignored non-OZC ticker {t.contract.localSymbol} (Class: {t.contract.tradingClass})")
                ignored_count += 1
                continue

            f_data = future_info_map.get(t.contract.lastTradeDateOrContractMonth)
            if not f_data:
                continue

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

        df_results = pd.DataFrame(results)
        if not df_results.empty:
            for expiry, group in df_results.groupby('expiry'):
                year = expiry[:4]
                try:
                    month_name = datetime.strptime(expiry[:6], '%Y%m').strftime('%B')
                except Exception:
                    month_name = "Unknown"
                logger.info(f"Successfully qualified {len(group)} OZC strikes for {month_name} {year}.")

        if ignored_count > 0:
            logger.info(f"Ignored {ignored_count} non-standard (Weekly/Short-Dated) contracts.")

        return df_results

    def _match_expiry(self, expiries, target_month):
        """Logic to match futures month or preceding month."""
        match = next((e for e in expiries if e.startswith(target_month)), None)
        if not match:
            try:
                fut_dt = datetime.strptime(target_month, '%Y%m')
                prev_dt = fut_dt - timedelta(days=15)
                prev_mo = prev_dt.strftime('%Y%m')
                match = next((e for e in expiries if e.startswith(prev_mo)), None)
            except Exception:
                pass

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
