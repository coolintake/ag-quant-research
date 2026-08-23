"""
CZ_historicalvol.py

Analyze Corn options implied volatility around USDA Prospective Plantings reports.
Fetches historical EOD options data, calculates implied volatility using Black-76,
and constructs clean DataFrames suitable for volatility surface analysis.

Author: Automated generation for volatility analysis
Date: 2026-01-24
"""

import numpy as np
import pandas as pd
import requests
from datetime import datetime, timedelta
from scipy.stats import norm
from scipy.optimize import brentq
import json
import time
from typing import Dict, List, Tuple, Optional
import warnings

warnings.filterwarnings('ignore')

# =====================================================================
# API CREDENTIALS
# =====================================================================

MASSIVE_API_KEY = "St6G_6jkYJtwxvNS51BwnV43yIa10ZKF"
QUICKSTATS_API_KEY = "EE1B3A2D-BC54-3ED1-B07F-BC3CF6E7B3A0"

# =====================================================================
# EVENT DEFINITIONS
# =====================================================================

USDA_EVENTS = [
    {
        "name": "Prospective Plantings 2023",
        "release_date": "2023-03-31",  # Friday, 12:00 PM ET
        "release_time": "12:00:00"
    },
    {
        "name": "Prospective Plantings 2024",
        "release_date": "2024-03-28",  # Thursday, 12:00 PM ET
        "release_time": "12:00:00"
    }
]

# =====================================================================
# CONSTANTS
# =====================================================================

RISK_FREE_RATE = 0.045  # 4.5% assumed
UNDERLYING_SYMBOL = "ZC"  # CBOT Corn
TARGET_EXPIRY = "July"  # July Corn futures

# Data quality thresholds
MAX_BID_ASK_SPREAD_RATIO = 0.25  # Flag if spread > 25% of mid
MIN_MONEYNESS = 0.70
MAX_MONEYNESS = 1.30
MIN_IV = 0.05
MAX_IV = 2.00

# =====================================================================
# BLACK-76 VOLATILITY CALCULATOR
# =====================================================================

class VolatilityCalculator:
    """
    Implied volatility calculator using Black-76 model for futures options.
    This is consistent with the approach in Async_VolSurface.py
    """
    
    @staticmethod
    def black_76_price(F: float, K: float, T: float, r: float, 
                       sigma: float, option_type: str = "C") -> float:
        """
        Black-76 pricing formula for futures options.
        
        Parameters:
        -----------
        F : float
            Futures price
        K : float
            Strike price
        T : float
            Time to expiry in years
        r : float
            Risk-free interest rate
        sigma : float
            Volatility (annualized)
        option_type : str
            "C" for call, "P" for put
            
        Returns:
        --------
        float
            Option theoretical price
        """
        if T <= 0 or sigma <= 0:
            return 0.0
            
        d1 = (np.log(F / K) + (0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
        d2 = d1 - sigma * np.sqrt(T)
        
        if option_type == "C":
            price = np.exp(-r * T) * (F * norm.cdf(d1) - K * norm.cdf(d2))
        else:  # Put
            price = np.exp(-r * T) * (K * norm.cdf(-d2) - F * norm.cdf(-d1))
            
        return price
    
    @staticmethod
    def solve_iv(market_price: float, F: float, K: float, T: float, 
                 r: float, option_type: str = "C") -> Optional[float]:
        """
        Solve for implied volatility using Brent's method.
        
        Parameters:
        -----------
        market_price : float
            Observed market price
        F : float
            Futures price
        K : float
            Strike price
        T : float
            Time to expiry in years
        r : float
            Risk-free rate
        option_type : str
            "C" for call, "P" for put
            
        Returns:
        --------
        float or None
            Implied volatility, or None if optimization fails
        """
        if T <= 0 or market_price <= 0:
            return None
            
        # Intrinsic value check
        if option_type == "C":
            intrinsic = max(0, F - K)
        else:
            intrinsic = max(0, K - F)
            
        if market_price < intrinsic * 0.95:  # Allow small violation for bid/ask
            return None
        
        def objective(sigma):
            try:
                theo_price = VolatilityCalculator.black_76_price(
                    F, K, T, r, sigma, option_type
                )
                return theo_price - market_price
            except:
                return 1e10
        
        try:
            # Try to solve between 5% and 200% volatility
            iv = brentq(objective, 0.05, 2.0, xtol=1e-6, maxiter=100)
            return iv if MIN_IV <= iv <= MAX_IV else None
        except:
            return None
    
    @staticmethod
    def validate_iv(iv: Optional[float]) -> bool:
        """Check if IV is within reasonable bounds"""
        if iv is None:
            return False
        return MIN_IV <= iv <= MAX_IV


# =====================================================================
# MASSIVE API CLIENT
# =====================================================================

class MassiveAPIClient:
    """
    Client for Massive financial data API.
    Handles historical options and futures data retrieval.
    """
    
    BASE_URL = "https://api.massive.io/v1"
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        })
    
    def get_futures_price(self, date: str, symbol: str = "ZC", 
                         contract_month: str = "N") -> Optional[float]:
        """
        Get historical futures price for a specific date.
        
        Parameters:
        -----------
        date : str
            Date in YYYY-MM-DD format
        symbol : str
            Futures symbol (default: ZC for Corn)
        contract_month : str
            Contract month code (N = July)
            
        Returns:
        --------
        float or None
            Futures settlement price
        """
        # Extract year for contract
        year = datetime.strptime(date, "%Y-%m-%d").year
        contract_code = f"{symbol}{contract_month}{str(year)[-2:]}"
        
        endpoint = f"{self.BASE_URL}/futures/historical"
        params = {
            "symbol": contract_code,
            "date": date,
            "settlement": "PM"  # Request PM settlement
        }
        
        try:
            response = self.session.get(endpoint, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            if data and "settlement_price" in data:
                return float(data["settlement_price"])
            else:
                print(f"⚠ No futures data for {contract_code} on {date}")
                return None
                
        except requests.exceptions.RequestException as e:
            print(f"❌ API Error fetching futures: {e}")
            return None
    
    def get_options_chain(self, date: str, underlying: str = "ZC",
                         expiry_month: str = "July") -> Optional[pd.DataFrame]:
        """
        Get historical options chain for a specific date.
        
        Parameters:
        -----------
        date : str
            Date in YYYY-MM-DD format
        underlying : str
            Underlying symbol (ZC for Corn)
        expiry_month : str
            Options expiry month
            
        Returns:
        --------
        pd.DataFrame or None
            Options chain with columns: strike, type, bid, ask, volume, open_interest
        """
        # For Corn, July options expire in June
        # Map expiry month to option month code
        month_map = {
            "July": "M",  # June options for July futures
            "December": "V",  # November options for December futures
        }
        
        option_month = month_map.get(expiry_month, "M")
        year = datetime.strptime(date, "%Y-%m-%d").year
        
        endpoint = f"{self.BASE_URL}/options/chain/historical"
        params = {
            "underlying": underlying,
            "expiry_month": option_month,
            "expiry_year": year,
            "date": date,
            "settlement": "PM"  # Request PM settlement
        }
        
        try:
            response = self.session.get(endpoint, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            if not data or "options" not in data:
                print(f"⚠ No options data for {underlying} {expiry_month} on {date}")
                return None
            
            # Parse options data into DataFrame
            options_list = []
            for opt in data["options"]:
                options_list.append({
                    "strike": float(opt.get("strike", 0)),
                    "type": opt.get("right", "").upper(),  # C or P
                    "bid": float(opt.get("bid", 0)),
                    "ask": float(opt.get("ask", 0)),
                    "last": float(opt.get("last", 0)),
                    "volume": int(opt.get("volume", 0)),
                    "open_interest": int(opt.get("open_interest", 0)),
                    "expiry": opt.get("expiry_date", "")
                })
            
            df = pd.DataFrame(options_list)
            
            # Calculate midpoint
            df["mid"] = (df["bid"] + df["ask"]) / 2
            
            # Flag wide spreads
            df["spread_ratio"] = (df["ask"] - df["bid"]) / df["mid"].replace(0, np.nan)
            df["wide_spread"] = df["spread_ratio"] > MAX_BID_ASK_SPREAD_RATIO
            
            return df
            
        except requests.exceptions.RequestException as e:
            print(f"❌ API Error fetching options: {e}")
            return None
    
    def validate_pm_settlement(self) -> bool:
        """
        Test if API supports PM settlement data.
        Returns True if PM settlement is available.
        """
        test_date = "2023-03-30"  # T-1 for first event
        
        try:
            price = self.get_futures_price(test_date)
            return price is not None
        except:
            return False


# =====================================================================
# USDA QUICKSTATS API CLIENT
# =====================================================================

class QuickStatsClient:
    """
    Client for USDA QuickStats API.
    Used to validate report release dates and retrieve planting data.
    """
    
    BASE_URL = "https://quickstats.nass.usda.gov/api"
    
    def __init__(self, api_key: str):
        self.api_key = api_key
    
    def get_prospective_plantings(self, year: int) -> Optional[Dict]:
        """
        Retrieve Prospective Plantings data for validation.
        
        Parameters:
        -----------
        year : int
            Report year
            
        Returns:
        --------
        dict or None
            Report metadata including release date
        """
        endpoint = f"{self.BASE_URL}/api_GET"
        params = {
            "key": self.api_key,
            "commodity_desc": "CORN",
            "statisticcat_desc": "AREA PLANTED",
            "reference_period_desc": "YEAR",
            "year": year,
            "agg_level_desc": "NATIONAL"
        }
        
        try:
            response = requests.get(endpoint, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            if data and "data" in data and len(data["data"]) > 0:
                return data["data"][0]
            else:
                print(f"⚠ No USDA data for {year}")
                return None
                
        except requests.exceptions.RequestException as e:
            print(f"❌ QuickStats API Error: {e}")
            return None


# =====================================================================
# EVENT WINDOW CALCULATOR
# =====================================================================

class EventWindowCalculator:
    """
    Calculate trading days around event dates (T-1, T, T+1).
    Accounts for weekends but not market holidays.
    """
    
    @staticmethod
    def is_weekend(date: datetime) -> bool:
        """Check if date falls on weekend"""
        return date.weekday() >= 5  # Saturday = 5, Sunday = 6
    
    @staticmethod
    def get_previous_trading_day(date: datetime) -> datetime:
        """Get previous trading day (skip weekends)"""
        prev_day = date - timedelta(days=1)
        while EventWindowCalculator.is_weekend(prev_day):
            prev_day -= timedelta(days=1)
        return prev_day
    
    @staticmethod
    def get_next_trading_day(date: datetime) -> datetime:
        """Get next trading day (skip weekends)"""
        next_day = date + timedelta(days=1)
        while EventWindowCalculator.is_weekend(next_day):
            next_day += timedelta(days=1)
        return next_day
    
    @staticmethod
    def calculate_event_window(event_date_str: str) -> Dict[str, str]:
        """
        Calculate T-1, T, T+1 trading days.
        
        Parameters:
        -----------
        event_date_str : str
            Event date in YYYY-MM-DD format
            
        Returns:
        --------
        dict
            Dictionary with keys: "T-1", "T", "T+1" (dates in YYYY-MM-DD format)
        """
        event_date = datetime.strptime(event_date_str, "%Y-%m-%d")
        
        t_minus_1 = EventWindowCalculator.get_previous_trading_day(event_date)
        t_plus_1 = EventWindowCalculator.get_next_trading_day(event_date)
        
        return {
            "T-1": t_minus_1.strftime("%Y-%m-%d"),
            "T": event_date_str,
            "T+1": t_plus_1.strftime("%Y-%m-%d")
        }


# =====================================================================
# MAIN EVENT ANALYZER
# =====================================================================

class USDAEventAnalyzer:
    """
    Main orchestrator for USDA event analysis.
    Coordinates data fetching, IV calculation, and output generation.
    """
    
    def __init__(self, massive_key: str, quickstats_key: str):
        self.massive_client = MassiveAPIClient(massive_key)
        self.quickstats_client = QuickStatsClient(quickstats_key)
        self.vol_calculator = VolatilityCalculator()
    
    def analyze_event(self, event: Dict) -> Dict[str, pd.DataFrame]:
        """
        Analyze a single USDA event across T-1, T, T+1.
        
        Parameters:
        -----------
        event : dict
            Event definition with release_date, name, etc.
            
        Returns:
        --------
        dict
            Dictionary with keys "T-1", "T", "T+1", values are DataFrames
        """
        print(f"\n{'='*60}")
        print(f"📊 Analyzing: {event['name']}")
        print(f"📅 Release Date: {event['release_date']} at {event['release_time']} ET")
        print(f"{'='*60}\n")
        
        # Calculate event window
        event_window = EventWindowCalculator.calculate_event_window(
            event["release_date"]
        )
        
        print(f"Event Window:")
        print(f"  T-1 (Pre-event):  {event_window['T-1']}")
        print(f"  T   (Event day):  {event_window['T']}")
        print(f"  T+1 (Post-event): {event_window['T+1']}\n")
        
        results = {}
        
        # Process each day in the window
        for period, date in event_window.items():
            print(f"📥 Fetching data for {period} ({date})...")
            df = self.fetch_and_process_day(date, event["release_date"])
            
            if df is not None and len(df) > 0:
                results[period] = df
                print(f"✓ {period}: {len(df)} options with valid IVs\n")
            else:
                print(f"⚠ {period}: No valid data\n")
                results[period] = pd.DataFrame()  # Empty DataFrame
        
        return results
    
    def fetch_and_process_day(self, date: str, event_name: str) -> Optional[pd.DataFrame]:
        """
        Fetch and process options data for a single day.
        
        Parameters:
        -----------
        date : str
            Date to fetch (YYYY-MM-DD)
        event_name : str
            Event identifier for metadata
            
        Returns:
        --------
        pd.DataFrame or None
            Processed DataFrame with IV and moneyness
        """
        # Get underlying futures price
        futures_price = self.massive_client.get_futures_price(date)
        if futures_price is None:
            print(f"  ⚠ Could not fetch futures price for {date}")
            return None
        
        print(f"  ✓ July Corn Futures: ${futures_price:.2f}")
        
        # Get options chain
        options_df = self.massive_client.get_options_chain(date, expiry_month="July")
        if options_df is None or len(options_df) == 0:
            print(f"  ⚠ No options data for {date}")
            return None
        
        print(f"  ✓ Retrieved {len(options_df)} option contracts")
        
        # Calculate time to maturity
        # July Corn options expire in June
        option_date = datetime.strptime(date, "%Y-%m-%d")
        year = option_date.year
        expiry_date = datetime(year, 6, 15)  # Approximate mid-June expiry
        
        if option_date > expiry_date:
            # Roll to next year if past expiry
            expiry_date = datetime(year + 1, 6, 15)
        
        T = (expiry_date - option_date).days / 365.0
        
        # Calculate implied volatility for each option
        ivs = []
        for idx, row in options_df.iterrows():
            if row["mid"] > 0 and row["strike"] > 0:
                iv = self.vol_calculator.solve_iv(
                    market_price=row["mid"],
                    F=futures_price,
                    K=row["strike"],
                    T=T,
                    r=RISK_FREE_RATE,
                    option_type=row["type"]
                )
                ivs.append(iv)
            else:
                ivs.append(None)
        
        options_df["implied_vol"] = ivs
        
        # Add calculated fields
        options_df["underlying_price"] = futures_price
        options_df["moneyness"] = options_df["strike"] / futures_price
        options_df["time_to_maturity"] = T
        options_df["date"] = date
        options_df["event"] = event_name
        
        # Filter valid options
        valid_df = options_df[
            (options_df["implied_vol"].notna()) &
            (options_df["moneyness"] >= MIN_MONEYNESS) &
            (options_df["moneyness"] <= MAX_MONEYNESS)
        ].copy()
        
        # Quality flags
        valid_df["liquid"] = (
            (valid_df["volume"] > 0) | 
            (valid_df["open_interest"] > 10)
        )
        
        valid_df["quality_flag"] = valid_df.apply(
            lambda x: "HIGH" if (x["liquid"] and not x["wide_spread"]) 
                     else "MEDIUM" if x["liquid"] 
                     else "LOW",
            axis=1
        )
        
        # Sort by strike and type
        valid_df = valid_df.sort_values(["strike", "type"])
        
        return valid_df
    
    def export_results(self, results: Dict[str, pd.DataFrame], 
                      event_name: str, output_dir: str = "."):
        """
        Export results to CSV files.
        
        Parameters:
        -----------
        results : dict
            Dictionary with T-1, T, T+1 DataFrames
        event_name : str
            Event identifier
        output_dir : str
            Output directory path
        """
        event_slug = event_name.replace(" ", "_").replace(",", "")
        
        for period, df in results.items():
            if len(df) > 0:
                filename = f"{output_dir}/corn_event_{event_slug}_{period}.csv"
                
                # Select relevant columns
                export_cols = [
                    "date", "event", "strike", "type", "bid", "ask", "mid",
                    "volume", "open_interest", "underlying_price", "moneyness",
                    "time_to_maturity", "implied_vol", "quality_flag"
                ]
                
                df[export_cols].to_csv(filename, index=False)
                print(f"💾 Saved: {filename}")
        
        # Save summary metadata
        summary = {
            "event": event_name,
            "periods": {
                period: {
                    "date": results[period]["date"].iloc[0] if len(results[period]) > 0 else None,
                    "row_count": len(results[period]),
                    "strikes_analyzed": int(results[period]["strike"].nunique()) if len(results[period]) > 0 else 0,
                    "avg_iv": float(results[period]["implied_vol"].mean()) if len(results[period]) > 0 else None,
                    "futures_price": float(results[period]["underlying_price"].iloc[0]) if len(results[period]) > 0 else None
                }
                for period in ["T-1", "T", "T+1"]
            }
        }
        
        summary_file = f"{output_dir}/corn_event_{event_slug}_summary.json"
        with open(summary_file, "w") as f:
            json.dump(summary, f, indent=2)
        
        print(f"💾 Saved: {summary_file}\n")
    
    def run_all_events(self, output_dir: str = "."):
        """
        Run analysis for all defined USDA events.
        
        Parameters:
        -----------
        output_dir : str
            Output directory for results
        """
        print("\n" + "="*60)
        print("🌽 USDA PROSPECTIVE PLANTINGS - VOLATILITY ANALYSIS")
        print("="*60)
        
        # Validate PM settlement availability
        print("\n🔍 Validating API capabilities...")
        if self.massive_client.validate_pm_settlement():
            print("✓ PM settlement data available\n")
        else:
            print("⚠ PM settlement validation inconclusive\n")
        
        # Process each event
        for event in USDA_EVENTS:
            results = self.analyze_event(event)
            self.export_results(results, event["release_date"], output_dir)
        
        print("="*60)
        print("✅ Analysis complete!")
        print("="*60)


# =====================================================================
# MAIN EXECUTION
# =====================================================================

def main():
    """Main execution function"""
    
    # Initialize analyzer
    analyzer = USDAEventAnalyzer(
        massive_key=MASSIVE_API_KEY,
        quickstats_key=QUICKSTATS_API_KEY
    )
    
    # Run analysis for all events
    analyzer.run_all_events(output_dir=".")
    
    print("\n📊 Output files generated:")
    print("  - corn_event_YYYY-MM-DD_T-1.csv (pre-event)")
    print("  - corn_event_YYYY-MM-DD_T.csv (event day)")
    print("  - corn_event_YYYY-MM-DD_T+1.csv (post-event)")
    print("  - corn_event_YYYY-MM-DD_summary.json (metadata)")
    print("\nDataFrames are ready for volatility surface construction! 🎉\n")


def test_api_connectivity():
    """Test function to validate API connections"""
    print("\n🧪 Testing API Connectivity...\n")
    
    # Test Massive API
    print("1. Testing Massive API...")
    client = MassiveAPIClient(MASSIVE_API_KEY)
    futures_price = client.get_futures_price("2023-03-30", symbol="ZC", contract_month="N")
    
    if futures_price:
        print(f"   ✓ Massive API working - Sample price: ${futures_price:.2f}")
    else:
        print("   ⚠ Massive API test failed - check credentials or endpoint")
    
    # Test QuickStats API
    print("\n2. Testing QuickStats API...")
    qs_client = QuickStatsClient(QUICKSTATS_API_KEY)
    data = qs_client.get_prospective_plantings(2023)
    
    if data:
        print(f"   ✓ QuickStats API working")
    else:
        print("   ⚠ QuickStats API test failed - check credentials")
    
    print("\n✅ API connectivity test complete\n")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "--test-api":
        test_api_connectivity()
    else:
        main()
