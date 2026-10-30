"""Unit conversions shared by every layer (rules: CONV in docs/METHODOLOGY.md)."""

from __future__ import annotations

import math

from ..config import PULSE_LBS_PER_BU, RULES, Crop, lb_per_unit


def round_yield(value: float) -> int:
    """[YIELD-ROUND] Target yields are whole numbers in their native unit (bu/ac, or lb/ac for
    pulses), halves rounded up. Every output (engine, JSON, workbook) uses this one function."""
    if RULES.yield_rounding != "whole_native_half_up":
        raise ValueError(f"unsupported yield_rounding {RULES.yield_rounding!r}")
    return int(math.floor(value + 0.5))


def price_per_bu(price_native: float, crop: Crop) -> float:
    """Native price ($/bu, or c/lb for pulses) -> $/bu."""
    return price_native / 100 * PULSE_LBS_PER_BU if crop.pulse else price_native


def price_native_from_bu(price_bu: float, crop: Crop) -> float:
    return price_bu / PULSE_LBS_PER_BU * 100 if crop.pulse else price_bu


def yield_bu(yield_native: float, crop: Crop) -> float:
    """Native yield (bu/ac, or lb/ac for pulses) -> bu/ac."""
    return yield_native / PULSE_LBS_PER_BU if crop.pulse else yield_native


def revenue(yield_native: float, price_native: float, crop: Crop) -> float:
    """Gross revenue $/ac: yield x price; pulses lb/ac x c/lb / 100."""
    return yield_native * price_native / (100 if crop.pulse else 1)


def quote_to_per_lb_product(price: float, unit: str) -> float:
    """Retail $/t or $/sh.t -> $ per lb of product."""
    return price / lb_per_unit(unit)
