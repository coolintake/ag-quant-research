"""Canonical crop baselines: one CropBaseline per (province, year, crop_key).

Every consumer (engine, Excel workbook, JSON output) reads these records; nothing downstream
re-derives a yield, a region average or a Last Yr fertilizer price on its own.

    * Builders are registered per source type (provinces.<code>.source.type), not per province:
      a new province plugs in a normalizer + a builder, no branch in the engine or workbook.
    * [REGION-ACTIVE] / [REGION-AVG-RULE] Only active regions enter a baseline: general crops
      average every active region where the guide publishes them, southern crops (durum, red
      lentils) Brown + Dark Brown only; simple (equal-weight) means; MB is province-wide.
    * [CROP-INHERIT] Crops with `inherits` (SK CPS / CNHR wheat) copy the parent's NPKS, seed,
      chemical, yield and price from their start year; they carry no history of their own.
    * [YIELD-ROUND] yield_whole = round_yield(yield_exact) is the yield every output uses.
    * [LASTYR-QUOTES] Last Yr retail quotes per province follow provinces.<code>.fertilizer.
      last_yr_quotes: "implied_from_guide" (SK) or "guide_published" (AB, MB); MB's applied N is
      the guide year's urea / NH3 blend [NCREDIT-MB] (n_shares).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from functools import lru_cache

from ..config import NUTRIENT_KEYS, PULSE_LBS_PER_BU, ProvinceSpec, crop, province
from ..pricing.fertilizer import per_lb, quotes_from_per_lb
from ..pricing.units import round_yield


@dataclass(frozen=True)
class CropBaseline:
    province: str
    year: int
    crop_key: str
    trade_label: str
    variety: str
    regions: tuple[str, ...]
    region_label: str
    price_basis: str                  # "$/bu" or "c/lb"
    yield_exact: float                # native unit: bu/ac, or lb/ac for pulses
    yield_whole: int                  # [YIELD-ROUND]
    price: float                      # native: $/bu, or c/lb for pulses
    seed: float                       # $/ac (incl. treatment)
    seed_treatment: float             # $/ac, where the guide itemizes it
    chem: float                       # $/ac
    rates: dict                       # lb/ac: N, P (P2O5), K (K2O), S
    seeding_lbs: float | None = None
    seeding_other: str | None = None
    seeding_note: str | None = None
    inherits: str | None = None
    guide_fertilizer: float | None = None          # the guide's own fertilizer $/ac
    guide_fert_by_nutrient: dict | None = None     # SK: printed N / P / K / S $/ac
    flags: tuple[str, ...] = ()
    provenance: dict = field(default_factory=dict)

    @property
    def pulse(self) -> bool:
        return self.price_basis == "c/lb"

    @property
    def yield_unit(self) -> str:
        return "lb/ac" if self.pulse else "bu/ac"

    @property
    def price_unit(self) -> str:
        return self.price_basis

    def to_dict(self) -> dict:
        d = {k: getattr(self, k) for k in self.__dataclass_fields__}
        d.update(yield_unit=self.yield_unit, price_unit=self.price_unit)
        return d


def _mean(vals) -> float | None:
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def _load(spec: ProvinceSpec) -> dict:
    path = spec.baseline_path("json")
    if not path.exists():
        raise FileNotFoundError(f"{spec.code} historical data not found: {path} "
                                "(run python -m src.pipeline --stage normalize)")
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Saskatchewan Crop Planning Guide (historical MVS records)
# ---------------------------------------------------------------------------
def _sk_record_to_baseline(spec: ProvinceSpec, rec: dict) -> CropBaseline:
    key = rec["crop_key"]
    c, pc = crop(key), spec.crop(key)
    rev, cost, a = rec["revenue"], rec["direct_costs_per_ac"], rec["agronomics"]
    y = rev["target_yield_lbs_ac"] if c.pulse else rev["target_yield_bu_ac"]
    basis = a.get("seeding_rate_basis") or ""
    by_name = {n: r for r, n in spec.regions.items()}
    regions = tuple(by_name[z] for z in rec.get("zones_used") or ()) or pc.regions   # where published
    note = None
    if basis.startswith("derived") or "units" in basis or "population" in basis:
        note = basis.split(" | ")[0] + (" (per zone, averaged)" if " | " in basis else "")
    return CropBaseline(
        province=spec.code, year=rec["year"], crop_key=key, trade_label=c.trade_label,
        variety=pc.variety or "", regions=regions, region_label=spec.region_label(regions),
        price_basis=c.price_basis, yield_exact=y, yield_whole=round_yield(y),
        price=rev["farm_price_cents_lb"] if c.pulse else rev["farm_price_per_bu"],
        seed=cost["seed_total"], seed_treatment=cost.get("seed_treatment") or 0.0, chem=cost["protection_total"],
        rates={"N": a.get("n_lbs_ac"), "P": a.get("p_lbs_ac"), "K": a.get("k_lbs_ac"), "S": a.get("s_lbs_ac")},
        seeding_lbs=a.get("seeding_rate_lbs_ac"), seeding_other=a.get("seeding_rate_other"), seeding_note=note,
        guide_fertilizer=cost.get("fert_total"),
        guide_fert_by_nutrient={"N": cost.get("fert_n"), "P": cost.get("fert_p"), "K": cost.get("fert_k"),
                                "S": cost.get("fert_s")},
        flags=tuple(rec.get("notes", [])), provenance={"source": rec.get("source", {}), "normalizer": "sk_mvs"},
    )


def _sk_baselines(spec: ProvinceSpec, year: int) -> list[CropBaseline]:
    recs = {r["crop_key"]: r for r in _load(spec)["records"] if r["year"] == year}
    if not recs:
        raise ValueError(f"no {spec.code} records for {year}")
    return [_sk_record_to_baseline(spec, recs[k]) for k in spec.source_crops() if k in recs]


def _sk_history(spec: ProvinceSpec, years) -> dict[int, dict[str, float]]:
    """Published SK history: return over direct cash inputs at whole yields, D from each
    year's guide line items."""
    out: dict[int, dict[str, float]] = {}
    for r in _load(spec)["records"]:
        if r["year"] in years:
            b = _sk_record_to_baseline(spec, r)
            rev = b.yield_whole * b.price / (100 if b.pulse else 1)
            out.setdefault(r["year"], {})[b.crop_key] = rev - r["direct_costs_per_ac"]["direct_cash_inputs"]
    return dict(sorted(out.items()))


# ---------------------------------------------------------------------------
# Alberta AgriProfit$ (per-region records -> region averages)
# ---------------------------------------------------------------------------
def _ab_baselines(spec: ProvinceSpec, year: int) -> list[CropBaseline]:
    recs = [r for r in _load(spec)["records"] if r["year"] == year]
    out = []
    for key in spec.source_crops():
        c, pc = crop(key), spec.crop(key)
        zr = sorted((r for r in recs if r["crop_key"] == key and r["zone"] in pc.regions),
                    key=lambda r: pc.regions.index(r["zone"]))
        if not zr:
            continue

        def y_native(r: dict) -> float | None:
            if r["yield_"] is None:
                return None
            if not c.pulse or r["yield_unit"] == "lb/ac":
                return r["yield_"]
            return r["yield_"] * PULSE_LBS_PER_BU

        def p_native(r: dict) -> float | None:
            if r["price"] is None:
                return None
            if not c.pulse:
                return r["price"]
            return r["price"] * 100 if r["price_unit"] == "$/lb" else r["price"] / PULSE_LBS_PER_BU * 100

        regions = tuple(r["zone"] for r in zr)
        rates_bu = _mean(r["seeding_rate"] for r in zr if r.get("seeding_rate_unit") == "bu/ac")
        y = _mean(y_native(r) for r in zr)
        out.append(CropBaseline(
            province=spec.code, year=year, crop_key=key, trade_label=c.trade_label,
            variety=", ".join(dict.fromkeys(r["grade"] for r in zr if r["grade"])),
            regions=regions, region_label=spec.region_label(regions), price_basis=c.price_basis,
            yield_exact=y, yield_whole=round_yield(y), price=_mean(p_native(r) for r in zr),
            seed=_mean(r["seed"] for r in zr), seed_treatment=0.0, chem=_mean(r["chemical"] for r in zr),
            rates={"N": _mean(r["n_lbs_ac"] for r in zr), "P": _mean(r["p2o5_lbs_ac"] for r in zr),
                   "K": _mean(r["k2o_lbs_ac"] for r in zr), "S": _mean(r["s_lbs_ac"] for r in zr)},
            seeding_lbs=_mean(r["seeding_rate_lbs_ac"] for r in zr),
            seeding_note=(f"{round(rates_bu, 2):g} bu/ac × {c.bu_weight:g} lb/bu" if rates_bu else None),
            guide_fertilizer=_mean(r["fertilizer"] for r in zr),
            flags=tuple(f"{spec.regions[r['zone']]}: {n}" for r in zr for n in r["notes"]
                        if n.startswith(("unverified", "core field", "seed $"))),
            provenance={"normalizer": "ab_cop", "regions": list(regions),
                        "pages": sorted({r["source"]["page"] for r in zr}), "file": zr[0]["source"]["file"]},
        ))
    return out


# ---------------------------------------------------------------------------
# Manitoba COP workbooks (province-wide records)
# ---------------------------------------------------------------------------
def _mb_baselines(spec: ProvinceSpec, year: int) -> list[CropBaseline]:
    recs = {r["crop_key"]: r for r in _load(spec)["records"] if r["year"] == year}
    out = []
    for key in spec.source_crops():
        r = recs.get(key)
        if r is None or r["yield_native"] is None:
            continue
        c, pc = crop(key), spec.crop(key)
        regions = (r["zone"],)
        rate, unit = r.get("seeding_rate"), r.get("seeding_rate_unit")
        out.append(CropBaseline(
            province=spec.code, year=year, crop_key=key, trade_label=c.trade_label, variety=pc.variety or "",
            regions=regions, region_label=spec.region_label(regions), price_basis=c.price_basis,
            yield_exact=r["yield_native"], yield_whole=round_yield(r["yield_native"]), price=r["price_native"],
            seed=r["seed"], seed_treatment=0.0, chem=r["chemical"],
            rates={"N": r["n_lbs_ac"], "P": r["p2o5_lbs_ac"], "K": r["k2o_lbs_ac"], "S": r["s_lbs_ac"]},
            seeding_lbs=r["seeding_rate_lbs_ac"], seeding_other=r.get("seeding_other"),
            seeding_note=(f"{rate:g} bu/ac × {c.bu_weight:g} lb/bu" if unit == "bu" and rate else None),
            guide_fertilizer=r["fertilizer"], guide_fert_by_nutrient=r.get("fert_by_nutrient"),
            flags=tuple(n for n in r["notes"] if n.startswith(("unverified", "core field", "guide caps"))),
            provenance={"normalizer": "mb_cop", "file": r["source"]["file"], "source_name": r["source_name"],
                        "summary_column": r["source"]["summary_column"], "cells": r["source"]["cells"]},
        ))
    return out


def _guide_history(spec: ProvinceSpec, years) -> dict[int, dict[str, float]]:
    """[HIST-RATIOS] each guide year's own return over direct cash inputs at whole yields: its
    bid x whole target yield - (seed + printed fertilizer + chemical). Same basis as SK's history
    (the guide's line items, not re-priced fertilizer)."""
    out: dict[int, dict[str, float]] = {}
    for y in sorted(years):
        for b in province_baselines(spec.code, y):
            if b.inherits:
                continue
            fert = b.guide_fertilizer
            if fert is None or b.price is None or b.seed is None or b.chem is None:
                continue
            rev = b.yield_whole * b.price / (100 if b.pulse else 1)
            out.setdefault(y, {})[b.crop_key] = rev - (b.seed + fert + b.chem)
    return out


# Registered per source type: {type: (baseline builder, history builder or None)}
BUILDERS = {
    "sk_crop_planning_guide": (_sk_baselines, _sk_history),
    "ab_agriprofit_cropping_alternatives": (_ab_baselines, _guide_history),
    "mb_cop_workbook": (_mb_baselines, _guide_history),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def _builder(spec: ProvinceSpec):
    if spec.source["type"] not in BUILDERS:
        raise NotImplementedError(f"no baseline builder for source type {spec.source['type']!r}")
    return BUILDERS[spec.source["type"]]


@lru_cache(maxsize=None)
def province_baselines(code: str, year: int) -> tuple[CropBaseline, ...]:
    """All baselines for a province-year, in the rules' crop order (inheriting crops included)."""
    spec = province(code)
    base = {b.crop_key: b for b in _builder(spec)[0](spec, year)}
    out = []
    for key, pc in spec.crops.items():
        if pc.inherits is None:
            if key in base:
                out.append(base[key])
            continue
        parent = base.get(pc.inherits)
        if parent is None or year < (pc.start_year or 0):
            continue
        c = crop(key)
        out.append(replace(
            parent, crop_key=key, trade_label=c.trade_label, variety=pc.variety or "", inherits=pc.inherits,
            price_basis=c.price_basis, seeding_note=f"follows {parent.trade_label}", guide_fert_by_nutrient=None,
            flags=(f"no guide history; NPKS, chemical, seed, yield and price start from {parent.trade_label} "
                   f"({year} guide) until class inputs are entered [CROP-INHERIT]",),
            provenance={"inherits": pc.inherits, "start_year": pc.start_year}))
    return tuple(out)


def baselines_by_key(code: str, year: int) -> dict[str, CropBaseline]:
    return {b.crop_key: b for b in province_baselines(code, year)}


def history(code: str, years) -> dict[int, dict[str, float]]:
    """Published per-year returns for provinces that have a history builder."""
    spec = province(code)
    hist = _builder(spec)[1]
    if hist is None:
        raise NotImplementedError(f"{code}: no history builder for {spec.source['type']!r}")
    return hist(spec, set(years))


def _implied_from_guide(spec: ProvinceSpec, year: int) -> dict[str, float]:
    """Retail quotes (to the cent) that reproduce the guide's mean implied $/lb per nutrient
    (fertilizer $/ac / lb/ac across source crops); defaults where the guide budgets none."""
    recs = [b for b in province_baselines(spec.code, year) if b.inherits is None and b.guide_fert_by_nutrient]
    implied = {}
    for n in NUTRIENT_KEYS:
        ratios = [b.guide_fert_by_nutrient[n] / b.rates[n] for b in recs
                  if b.rates.get(n) and b.guide_fert_by_nutrient.get(n) is not None]
        implied[n] = sum(ratios) / len(ratios) if ratios else spec.default_per_lb[n]
    return {k: round(v, 2) for k, v in quotes_from_per_lb(implied, spec).items()}


def _guide_published(spec: ProvinceSpec, year: int) -> dict[str, float]:
    prices = _load(spec)["meta"]["fert_prices"][str(year)]
    return {n: prices[n]["per_tonne"] for n in spec.quote_keys}


QUOTE_SOURCES = {"implied_from_guide": _implied_from_guide, "guide_published": _guide_published}


@lru_cache(maxsize=None)
def _last_yr_quotes(code: str, year: int) -> tuple[tuple[str, float], ...]:
    spec = province(code)
    return tuple(QUOTE_SOURCES[spec.last_yr_quotes](spec, year).items())


def last_yr_quotes(code: str, year: int) -> dict[str, float]:
    """Last Yr retail quotes in the province's quote unit."""
    return dict(_last_yr_quotes(code, year))


def n_shares(code: str, year: int) -> dict[str, float]:
    """[NCREDIT-MB] the guide year's share of applied N from each blended source ({} without a blend)."""
    spec = province(code)
    if not spec.n_blend:
        return {}
    shares = _load(spec).get("meta", {}).get("n_shares", {}).get(str(year))
    if shares is None:
        raise KeyError(f"{code} {year}: no N source shares in the normalized data")
    return {k: float(shares.get(k, 0.0)) for k in spec.n_blend}


def last_yr_per_lb(code: str, year: int) -> dict[str, float]:
    return per_lb(last_yr_quotes(code, year), province(code), n_shares=n_shares(code, year))


def guide_published_per_lb(code: str, year: int) -> dict[str, float] | None:
    """The guide's own printed $/lb (AB); None where the guide prints no nutrient prices."""
    prices = _load(province(code)).get("meta", {}).get("fert_prices", {}).get(str(year))
    return {n: prices[n]["per_lb"] for n in NUTRIENT_KEYS} if prices else None
