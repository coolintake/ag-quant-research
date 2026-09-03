"""
models/black76.py
==================
Stateless Black-76 futures option pricing and implied volatility solver.
Pure math -- no I/O, no state, no dependency on the rest of the engine.
"""

import numpy as np
from scipy.stats import norm
from scipy.optimize import brentq


def black_76_price(F, K, T, r, sigma, option_type="C"):
    """Black-76 theoretical price for a futures option."""
    if T <= 0:
        return max(0, F - K) if option_type == "C" else max(0, K - F)
    d1 = (np.log(F / K) + (sigma**2 / 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    disc = np.exp(-r * T)
    if option_type == "C":
        return disc * (F * norm.cdf(d1) - K * norm.cdf(d2))
    else:
        return disc * (K * norm.cdf(-d2) - F * norm.cdf(-d1))


def solve_iv(F, K, T, r, option_type, market_price):
    """Solves for implied volatility given a market price via Brent's method."""
    intrinsic = max(0, F - K) if option_type == "C" else max(0, K - F)
    if market_price <= intrinsic + 1e-4:
        return np.nan

    def objective(sigma):
        return black_76_price(F, K, T, r, sigma, option_type) - market_price

    try:
        return brentq(objective, 1e-4, 4.0, xtol=1e-5)
    except Exception:
        return np.nan
