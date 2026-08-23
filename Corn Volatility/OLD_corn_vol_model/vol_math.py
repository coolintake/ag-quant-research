import numpy as np
import pandas as pd
from scipy.optimize import brentq
from scipy.stats import norm
from scipy.interpolate import griddata

class VolMath:
    @staticmethod
    def bs_price(S, K, T, r, sigma, option_type="C"):
        if T <= 0: return max(S - K, 0) if option_type == "C" else max(K - S, 0)
        d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
        d2 = d1 - sigma * np.sqrt(T)
        if option_type == "C":
            return S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
        return K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)

    @staticmethod
    def solve_iv(market_price, S, K, T, r, option_type="C"):
        intrinsic = max(S - K, 0) if option_type == "C" else max(K - S, 0)
        if market_price <= intrinsic or T <= 0: return np.nan
        
        objective = lambda sigma: VolMath.bs_price(S, K, T, r, sigma, option_type) - market_price
        try:
            return brentq(objective, 1e-4, 3.0, xtol=1e-6)
        except:
            return np.nan

    @staticmethod
    def construct_surface(df, num_points=50):
        """df must have columns: strike, time_to_expiry, iv"""
        s_range = np.linspace(df['strike'].min(), df['strike'].max(), num_points)
        t_range = np.linspace(df['time_to_expiry'].min(), df['time_to_expiry'].max(), num_points)
        S_mesh, T_mesh = np.meshgrid(s_range, t_range)
        
        IV_mesh = griddata(
            (df['strike'], df['time_to_expiry']), df['iv'],
            (S_mesh, T_mesh), method='cubic'
        )
        return S_mesh, T_mesh, IV_mesh