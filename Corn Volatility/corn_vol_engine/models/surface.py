"""
models/surface.py
==================
Volatility surface construction: raw data cleaning and IV extraction,
bivariate spline fitting, arbitrage validation (butterfly + calendar),
and fit-quality metrics. Built on top of models.black76.
"""

import logging
from datetime import datetime

import numpy as np
import pandas as pd
from scipy.interpolate import SmoothBivariateSpline

from config import CONFIG
from models.black76 import black_76_price, solve_iv

logger = logging.getLogger(__name__)


class VolatilityEngine:
    """Stateless calculations and surface fitting over cleaned option data."""

    @staticmethod
    def process_data(df):
        """Clean and calculate IVs and Moneyness."""
        if df.empty:
            return df

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
            except Exception:
                return -1

        df['days_to_expiry'] = df['expiry'].apply(calc_dte)
        df['T'] = df['days_to_expiry'] / 365.0

        # 2. Basic Filters
        df = df[df['days_to_expiry'] > CONFIG['min_dte']].copy()

        # Handle missing volume column in legacy CSV
        if 'volume' not in df.columns:
            df['volume'] = 0

        # Handle missing 'right' column (Call/Put)
        if 'right' not in df.columns:
            # If Strike >= Underlying -> It's a Call (Upside)
            # If Strike < Underlying -> It's a Put (Downside)
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
            # Closest strike to underlying, strictly within each expiry group
            df['atm_strike'] = df.groupby('expiry')['strike'].transform(
                lambda strikes: strikes.iloc[(strikes - df.loc[strikes.index, 'und_price'].iloc[0]).abs().argmin()]
            )

        # Relaxed volume filter for combined Offline Mode.
        # Why? CSV data might have 0 volume but valid mid prices for visualization.
        initial_len = len(df)
        if CONFIG['offline_mode']:
            if 'iv' in df.columns:
                df = df[(df['volume'] > 0) | (df['iv'].notna()) | (df['bid'] > 0)].copy()
            else:
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
                # Prefer bid/ask mid when both are valid
                if row['bid'] > 0 and row['ask'] > 0:
                    mid = (row['bid'] + row['ask']) / 2

                    # Spread quality check
                    rel_spread = (row['ask'] - row['bid']) / mid
                    if rel_spread > CONFIG['max_relative_spread']:
                        return np.nan

                    return mid

                # If no valid bid/ask, use 'last' as fallback IF it exists
                if row.get('last', 0) > 0:
                    return row['last']

                return np.nan

            ivs = []
            for _, row in group.iterrows():
                mid = get_mid(row)
                if np.isnan(mid):
                    continue

                iv = solve_iv(row['und_price'], strike, row['T'], CONFIG['risk_free_rate'], row['right'], mid)
                # Fallback to model_iv if it exists in the data
                if np.isnan(iv) and 'model_iv' in row.index and not pd.isna(row['model_iv']):
                    iv = row['model_iv']
                if not np.isnan(iv):
                    ivs.append(iv)

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
        if len(df) < 10:
            return None
        clean = df.dropna(subset=['iv']).sort_values(['days_to_expiry', 'moneyness_kf'])
        try:
            kx = min(3, len(clean['moneyness_kf'].unique()) - 1)
            ky = min(3, len(clean['days_to_expiry'].unique()) - 1)
            if kx < 1 or ky < 1:
                return None
            return SmoothBivariateSpline(
                clean['moneyness_kf'], clean['days_to_expiry'], clean['iv'], kx=kx, ky=ky, s=len(clean)
            )
        except Exception:
            return None

    @staticmethod
    def check_butterfly_arbitrage(df):
        """Detect convexity violations (Negative Butterflies)."""
        violations = []
        for (expiry, right), group in df.groupby(['expiry', 'right']):
            group = group.sort_values('strike')
            if len(group) < 3:
                continue

            # Use real mid/last market price wherever available. The Black-76
            # theoretical price is only a last-resort fallback when no market
            # price exists at all (missing bid/ask/last) -- a theoretical price
            # can't itself reveal a market mispricing, so any strike relying on
            # this fallback is flagged and excluded from triggering a violation.
            strikes = group['strike'].values
            prices = []
            is_theoretical = []
            for _, row in group.iterrows():
                p = (row['bid'] + row['ask']) / 2 if row['bid'] > 0 and row['ask'] > 0 else row['last']
                theoretical = np.isnan(p) or p <= 0
                if theoretical:
                    p = black_76_price(row['und_price'], row['strike'], row['T'], CONFIG['risk_free_rate'], row['iv'], row['right'])
                    logger.warning(
                        f"BUTTERFLY_PROXY: {right} strike {row['strike']} in {expiry} has no market "
                        f"price; using theoretical Black-76 fallback (cannot itself indicate a violation)."
                    )
                prices.append(p)
                is_theoretical.append(theoretical)

            for i in range(1, len(strikes) - 1):
                # Skip any triplet touching a theoretical (non-market) price --
                # it can only manufacture or mask a violation, never confirm one.
                if is_theoretical[i - 1] or is_theoretical[i] or is_theoretical[i + 1]:
                    continue

                k1, k2, k3 = strikes[i - 1], strikes[i], strikes[i + 1]
                p1, p2, p3 = prices[i - 1], prices[i], prices[i + 1]

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

        # Create moneyness buckets (5% increments), e.g. 0.95, 1.00, 1.05, 1.10
        df = df.copy()
        df['moneyness_bucket'] = (df['moneyness_kf'] * 20).round() / 20

        # Group by moneyness bucket (not absolute strike)
        for bucket, group in df.groupby('moneyness_bucket'):
            group = group.sort_values('T')

            if len(group) < 2:
                continue  # Need at least 2 expiries to compare

            prev_total_var = -1
            for _, row in group.iterrows():
                total_var = (row['iv'] ** 2) * row['T']

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
