"""Active nutrient pricing: retail product quotes -> $/lb N, P2O5, K2O, S, per province.

[NCREDIT] One rule for every province, parameters from docs/AGENT_RULES.json:

    $/lb N        = quote_N / lb per unit / grade_N
    $/lb nutrient = (quote / lb per unit - N content x $/lb N) / grade      (P, K, S)

The N carried by MAP (11-52-0) and ammonium sulphate is credited at the province's own
$/lb N, so the same quotes give the same nutrient vector on either side of a border, while
regional freight / basis in the quotes produces distinct vectors per province.

[NCREDIT-MB] A province with an N blend (provinces.<code>.fertilizer.n_blend; MB: urea + NH3)
prices applied N at the guide's source shares, while the MAP / AS credit stays at the N
product's (urea) $/lb, exactly as the MB COP workbooks do:

    $/lb N applied = (1 - share) x $/lb N(urea) + share x quote_NH3 / lb per unit / 0.82
"""

from __future__ import annotations

from ..config import NUTRIENT_KEYS, ProvinceSpec, lb_per_unit


def per_lb(quotes: dict[str, float], spec: ProvinceSpec, unit: str | None = None,
           n_shares: dict[str, float] | None = None) -> dict[str, float]:
    """Quotes {N, P, K, S (+ blend sources)} in `unit` ($/t or $/sh.t; default the province's
    quote unit) -> $/lb {N, P, K, S}. `n_shares` {source: share of applied N} for a blend."""
    shares = {k: v for k, v in (n_shares or {}).items() if v}
    missing = (set(NUTRIENT_KEYS) | set(shares)) - set(quotes)
    if missing:
        raise ValueError(f"{spec.code} quotes need {', '.join(NUTRIENT_KEYS + tuple(shares))} (missing {sorted(missing)})")
    if set(shares) - set(spec.n_blend):
        raise ValueError(f"{spec.code} has no N blend source {sorted(set(shares) - set(spec.n_blend))}")
    lb = lb_per_unit(unit or spec.quote_unit)
    prod = spec.products
    n = quotes["N"] / lb / prod["N"].grade
    out = {"N": (1 - sum(shares.values())) * n + sum(s * quotes[k] / lb / spec.n_blend[k].grade
                                                    for k, s in shares.items())}
    for k in ("P", "K", "S"):
        out[k] = (quotes[k] / lb - prod[k].n_content * n) / prod[k].grade
    return out


def quotes_from_per_lb(prices: dict[str, float], spec: ProvinceSpec, unit: str | None = None) -> dict[str, float]:
    """Inverse of per_lb (single N product): the quotes that net to the given $/lb vector."""
    lb = lb_per_unit(unit or spec.quote_unit)
    prod, n = spec.products, prices["N"]
    return {k: (prices[k] * prod[k].grade + prod[k].n_content * n) * lb for k in NUTRIENT_KEYS}


def convert_quote(price: float, from_unit: str, to_unit: str) -> float:
    """$/t <-> $/sh.t for the same product."""
    return price / lb_per_unit(from_unit) * lb_per_unit(to_unit)
