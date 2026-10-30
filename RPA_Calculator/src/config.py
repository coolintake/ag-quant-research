"""Configuration: loads docs/AGENT_RULES.json, validates it strictly and exposes it as a typed
registry (crops, provinces, fertilizer benchmarks, overrides) plus derived paths.

No rule lives in this file. To change an active region, a crop alias, an N-credit parameter
or a source override, edit docs/AGENT_RULES.json (and its entry in docs/METHODOLOGY.md);
load_rules() refuses a rules file that is inconsistent.

    python -m src.config            # validate the rules and list each province's source files
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RULES_PATH = PROJECT_ROOT / "docs" / "AGENT_RULES.json"
OUTPUT_DIR = PROJECT_ROOT / "output"
PRICE_INPUT_DIR = PROJECT_ROOT / "Input_Prices"
INPUT_PRICE_TEMPLATE = PRICE_INPUT_DIR / "Input_price_template.csv"     # benchmark documentation
FERTILIZER_QUOTES = PRICE_INPUT_DIR / "fertilizer_quotes.csv"            # dated retail quotes by province
CROP_PRICE_TEMPLATE = PRICE_INPUT_DIR / "crop_price_template.csv"       # dated grain bids / yields
MASTER_WORKBOOK = OUTPUT_DIR / "RPA_Master_Calculator.xlsx"

NUTRIENT_KEYS = ("N", "P", "K", "S")
PRICE_BASES = ("$/bu", "c/lb")
QUOTE_UNITS = ("$/t", "$/sh.t")
SEED_RATE_UNITS = ("bu/ac", "lb/ac")
YIELD_ROUNDING = ("whole_native_half_up",)
LAST_YR_QUOTE_SOURCES = ("implied_from_guide", "guide_published")
REGION_SCOPES = ("soil_zones", "province_wide")
OVERRIDE_FIELDS = {"seed": ("table1_cost",)}
PDF_YEAR_PATTERN = re.compile(r"(20\d{2})")


class RulesError(ValueError):
    """docs/AGENT_RULES.json is missing a field or contradicts itself."""


# ---------------------------------------------------------------------------
# Typed registry
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Crop:
    key: str
    trade_label: str
    price_basis: str            # "$/bu" or "c/lb"
    bu_weight: float            # lb per bushel
    seed_rate_unit: str         # how guides state the seeding rate

    @property
    def pulse(self) -> bool:
        return self.price_basis == "c/lb"


@dataclass(frozen=True)
class FertProduct:
    nutrient: str
    product: str
    analysis: str
    grade: float                # nutrient content of the product (0.46 for urea)
    n_content: float            # lb N per lb product, credited at the $/lb N


@dataclass(frozen=True)
class ProvinceCrop:
    key: str
    regions: tuple[str, ...]
    variety: str | None
    source_aliases: tuple[str, ...]
    source_patterns: tuple[str, ...]
    inherits: str | None
    start_year: int | None

    @property
    def method(self) -> str:
        return "blend" if len(self.regions) > 1 else "single"


@dataclass(frozen=True)
class ProvinceSpec:
    code: str
    name: str
    rule_id: str
    source: dict
    regions: dict[str, str]
    active_regions: tuple[str, ...]
    quote_unit: str
    products: dict[str, FertProduct]
    last_yr_quotes: str
    default_per_lb: dict[str, float]
    crops: dict[str, ProvinceCrop]            # ordered as in the rules (workbook column order)
    agronomy_rules: tuple[dict, ...]
    region_scope: str = "soil_zones"          # "soil_zones" or "province_wide" [REGION-AVG-RULE]
    n_blend: dict[str, FertProduct] = field(default_factory=dict)   # secondary N sources (MB: NH3)

    @property
    def quote_keys(self) -> tuple[str, ...]:
        """Retail quotes a province takes: N (credit product), P, K, S, then any blended N source."""
        return (*NUTRIENT_KEYS, *self.n_blend)

    # ---- paths (derived, never configured by hand)
    @property
    def input_dir(self) -> Path:
        return PROJECT_ROOT / self.source["input_dir"]

    @property
    def output_dir(self) -> Path:
        return OUTPUT_DIR / self.code.lower()

    @property
    def intermediate_dir(self) -> Path:
        return self.output_dir / "intermediate"

    @property
    def ocr_cache_dir(self) -> Path:
        return self.intermediate_dir / "ocr"

    @property
    def raw_json(self) -> Path:
        return self.intermediate_dir / f"{self.code.lower()}_raw_tables.json"

    @property
    def baseline_dir(self) -> Path:
        return self.output_dir / "mvs"

    def baseline_path(self, ext: str = "json") -> Path:
        y0, y1 = RULES.years
        return self.baseline_dir / f"{self.code.lower()}_historical_{y0}_{y1}.{ext}"

    @property
    def scenario_dir(self) -> Path:
        return self.output_dir / "scenarios"

    # ---- helpers
    def crop(self, key: str) -> ProvinceCrop:
        if key not in self.crops:
            raise KeyError(f"{self.code} has no crop {key!r}")
        return self.crops[key]

    def region_label(self, regions) -> str:
        names = [self.regions[r] for r in regions]
        return " + ".join(names) + (" (avg)" if len(names) > 1 else "")

    def source_crops(self) -> list[str]:
        """Crops read from the guides (excludes classes that inherit a parent)."""
        return [k for k, c in self.crops.items() if c.inherits is None]


@dataclass(frozen=True)
class Rules:
    schema_version: str
    years: tuple[int, int]
    baseline_year: int
    yield_rounding: str
    pulse_lbs_per_bu: float
    lb_per_unit: dict[str, float]
    crops: dict[str, Crop]
    provinces: dict[str, ProvinceSpec]
    source_overrides: tuple[dict, ...]
    historical: dict
    workbook_tabs: tuple[str, ...]
    raw: dict


# ---------------------------------------------------------------------------
# Loader + validator
# ---------------------------------------------------------------------------
def load_rules(path: Path = RULES_PATH) -> Rules:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RulesError(f"rules file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RulesError(f"{path.name} is not valid JSON: {exc}") from exc
    errors: list[str] = []

    def need(obj: dict, key: str, where: str, kind=None):
        if not isinstance(obj, dict) or key not in obj:
            errors.append(f"{where}: missing '{key}'")
            return None
        val = obj[key]
        if kind is not None and not isinstance(val, kind):
            errors.append(f"{where}.{key}: expected {getattr(kind, '__name__', kind)}, got {type(val).__name__}")
            return None
        return val

    conv = need(raw, "conventions", "rules", dict) or {}
    years = need(conv, "years", "conventions", list) or [0, 0]
    if len(years) != 2 or not all(isinstance(y, int) for y in years) or years[0] > years[1]:
        errors.append("conventions.years: expected [first_year, last_year]")
    baseline_year = need(conv, "baseline_year", "conventions", int) or 0
    if years and len(years) == 2 and not (years[0] <= baseline_year <= years[1]):
        errors.append("conventions.baseline_year: outside conventions.years")
    rounding = need(conv, "yield_rounding", "conventions", str)
    if rounding not in YIELD_ROUNDING:
        errors.append(f"conventions.yield_rounding: must be one of {YIELD_ROUNDING}")
    if conv.get("pulse_price_basis") != "c/lb":
        errors.append("conventions.pulse_price_basis: must be 'c/lb'")
    pulse_lb = need(conv, "pulse_lbs_per_bu", "conventions", (int, float)) or 0
    lb_per_unit = need(conv, "lb_per_unit", "conventions", dict) or {}
    for u in QUOTE_UNITS:
        if not isinstance(lb_per_unit.get(u), (int, float)) or lb_per_unit.get(u, 0) <= 0:
            errors.append(f"conventions.lb_per_unit: needs a positive value for {u!r}")

    # [REGION-AVG-RULE] one averaging rule for every province: general crops use all active
    # regions, southern crops only the listed southern regions (within each province's active set).
    avg = need(raw, "region_averaging", "rules", dict) or {}
    if avg.get("general_regions") != "active_regions":
        errors.append("region_averaging.general_regions: must be 'active_regions'")
    southern_crops = tuple(need(avg, "southern_crops", "region_averaging", list) or ())
    southern_regions = tuple(need(avg, "southern_regions", "region_averaging", list) or ())
    if not southern_regions:
        errors.append("region_averaging.southern_regions: at least one region")

    crops: dict[str, Crop] = {}
    labels: set[str] = set()
    for key, c in (need(raw, "crops", "rules", dict) or {}).items():
        where = f"crops.{key}"
        if not re.fullmatch(r"[a-z][a-z0-9_]*", key):
            errors.append(f"{where}: crop_key must be snake_case")
        label = need(c, "trade_label", where, str)
        basis = need(c, "price_basis", where, str)
        weight = need(c, "bu_weight", where, (int, float))
        unit = need(c, "seed_rate_unit", where, str)
        if basis and basis not in PRICE_BASES:
            errors.append(f"{where}.price_basis: must be one of {PRICE_BASES}")
        if unit and unit not in SEED_RATE_UNITS:
            errors.append(f"{where}.seed_rate_unit: must be one of {SEED_RATE_UNITS}")
        if weight is not None and weight <= 0:
            errors.append(f"{where}.bu_weight: must be positive")
        if label in labels:
            errors.append(f"{where}.trade_label: {label!r} used twice")
        labels.add(label)
        if None not in (label, basis, weight, unit):
            crops[key] = Crop(key, label, basis, float(weight), unit)

    provinces: dict[str, ProvinceSpec] = {}
    for code, p in (need(raw, "provinces", "rules", dict) or {}).items():
        where = f"provinces.{code}"
        if not re.fullmatch(r"[A-Z]{2}", code):
            errors.append(f"{where}: province code must be two capital letters")
        src = need(p, "source", where, dict) or {}
        for k in ("type", "title", "input_dir", "file_glob"):
            need(src, k, f"{where}.source", str)
        regions = need(p, "regions", where, dict) or {}
        active = tuple(need(p, "active_regions", where, list) or [])
        scope = need(p, "region_scope", where, str) or "soil_zones"
        if scope not in REGION_SCOPES:
            errors.append(f"{where}.region_scope: must be one of {REGION_SCOPES}")
        if scope == "province_wide" and len(active) != 1:
            errors.append(f"{where}.active_regions: a province-wide source has exactly one region")
        if not active:
            errors.append(f"{where}.active_regions: at least one region")
        for r in active:
            if r not in regions:
                errors.append(f"{where}.active_regions: unknown region {r!r}")
        fert = need(p, "fertilizer", where, dict) or {}
        qunit = need(fert, "quote_unit", f"{where}.fertilizer", str)
        if qunit and qunit not in QUOTE_UNITS:
            errors.append(f"{where}.fertilizer.quote_unit: must be one of {QUOTE_UNITS}")
        last_src = need(fert, "last_yr_quotes", f"{where}.fertilizer", str)
        if last_src and last_src not in LAST_YR_QUOTE_SOURCES:
            errors.append(f"{where}.fertilizer.last_yr_quotes: must be one of {LAST_YR_QUOTE_SOURCES}")
        products: dict[str, FertProduct] = {}
        for n in NUTRIENT_KEYS:
            pr = (fert.get("products") or {}).get(n)
            pw = f"{where}.fertilizer.products.{n}"
            if not isinstance(pr, dict):
                errors.append(f"{pw}: missing")
                continue
            grade, ncon = pr.get("grade"), pr.get("n_content")
            if not isinstance(grade, (int, float)) or not 0 < grade <= 1:
                errors.append(f"{pw}.grade: must be in (0, 1]")
                continue
            if not isinstance(ncon, (int, float)) or not 0 <= ncon < 1:
                errors.append(f"{pw}.n_content: must be in [0, 1)")
                continue
            if n == "N" and ncon != 0:
                errors.append(f"{pw}.n_content: the N product carries no N credit (must be 0)")
            products[n] = FertProduct(n, need(pr, "product", pw, str) or "", need(pr, "analysis", pw, str) or "",
                                      float(grade), float(ncon))
        # [NCREDIT] a province may price applied N as a blend of N sources (MB: urea + NH3, shares
        # from each guide year). The N in MAP / AS is still credited at the N product's $/lb.
        n_blend: dict[str, FertProduct] = {}
        for k, pr in (fert.get("n_blend") or {}).items():
            pw = f"{where}.fertilizer.n_blend.{k}"
            grade = pr.get("grade") if isinstance(pr, dict) else None
            if k in NUTRIENT_KEYS or not re.fullmatch(r"[A-Z][A-Z0-9]*", k):
                errors.append(f"{pw}: key must be a new upper-case code (not N/P/K/S)")
            elif not isinstance(grade, (int, float)) or not 0 < grade <= 1:
                errors.append(f"{pw}.grade: must be in (0, 1]")
            else:
                n_blend[k] = FertProduct("N", need(pr, "product", pw, str) or "", need(pr, "analysis", pw, str) or "",
                                         float(grade), 0.0)
        if len(n_blend) > 1:
            errors.append(f"{where}.fertilizer.n_blend: at most one secondary N source")
        defaults = fert.get("default_per_lb", {}) or {}
        for n, v in defaults.items():
            if n not in NUTRIENT_KEYS or not isinstance(v, (int, float)) or v < 0:
                errors.append(f"{where}.fertilizer.default_per_lb.{n}: nutrient N/P/K/S with a price >= 0")

        pcrops: dict[str, ProvinceCrop] = {}
        seen_alias: dict[str, str] = {}
        raw_crops = need(p, "crops", where, dict) or {}
        for key, c in raw_crops.items():
            cw = f"{where}.crops.{key}"
            if key not in crops:
                errors.append(f"{cw}: crop_key not in the crop registry")
                continue
            inherits = c.get("inherits")
            if "regions" in c:
                errors.append(f"{cw}.regions: set by region_averaging [REGION-AVG-RULE], not per crop")
            regions_c = (tuple(r for r in active if r in southern_regions)
                         if key in southern_crops and scope == "soil_zones" else active)
            aliases = tuple(c.get("source_aliases") or ())
            patterns = tuple(c.get("source_patterns") or ())
            for pat in patterns:
                try:
                    re.compile(pat)
                except re.error as exc:
                    errors.append(f"{cw}.source_patterns: {pat!r} does not compile ({exc})")
            if inherits is None and not (aliases or patterns):
                errors.append(f"{cw}: needs source_aliases or source_patterns (or inherits)")
            if inherits is not None:
                parent = raw_crops.get(inherits)
                if parent is None:
                    errors.append(f"{cw}.inherits: {inherits!r} is not a {code} crop")
                elif parent.get("inherits"):
                    errors.append(f"{cw}.inherits: {inherits!r} itself inherits (one level only)")
                if not isinstance(c.get("start_year"), int):
                    errors.append(f"{cw}.start_year: required for an inheriting crop")
            for a in aliases:
                norm = normalize_label(a)
                if norm in seen_alias and seen_alias[norm] != key:
                    errors.append(f"{cw}.source_aliases: {a!r} also maps to {seen_alias[norm]}")
                seen_alias[norm] = key
            pcrops[key] = ProvinceCrop(key, regions_c, c.get("variety"), aliases, patterns, inherits,
                                       c.get("start_year"))
        rule_ids = [r.get("id") for r in p.get("agronomy_rules", [])]
        if len(rule_ids) != len(set(rule_ids)) or not all(rule_ids):
            errors.append(f"{where}.agronomy_rules: ids must be present and unique")
        if not products or len(products) < 4:
            continue
        provinces[code] = ProvinceSpec(code, need(p, "name", where, str) or code, p.get("rule_id", f"PROV-{code}"),
                                       src, regions, active, qunit or "$/t", products, last_src or "",
                                       {k: float(v) for k, v in defaults.items()}, pcrops,
                                       tuple(p.get("agronomy_rules", [])), scope, n_blend)

    # display order for every province listing (tabs, tables, JSON): conventions.province_order
    order = conv.get("province_order") or list(provinces)
    if sorted(order) != sorted(provinces):
        errors.append(f"conventions.province_order: must list each province once ({sorted(provinces)})")
    else:
        provinces = {code: provinces[code] for code in order}

    for key in southern_crops:
        if key not in crops:
            errors.append(f"region_averaging.southern_crops: {key!r} is not in the crop registry")
    for code, spec in provinces.items():
        if spec.region_scope == "soil_zones" and not set(southern_regions) <= set(spec.active_regions):
            errors.append(f"region_averaging.southern_regions: {sorted(set(southern_regions) - set(spec.active_regions))} "
                          f"not active in {code}")

    overrides = tuple(raw.get("source_overrides", []))
    ids = set()
    for o in overrides:
        oid = o.get("id")
        if not oid or oid in ids:
            errors.append(f"source_overrides: id missing or duplicated ({oid!r})")
        ids.add(oid)
        prov = provinces.get(o.get("province"))
        if prov is None:
            errors.append(f"source_overrides.{oid}: unknown province {o.get('province')!r}")
        elif o.get("crop_key") not in prov.crops:
            errors.append(f"source_overrides.{oid}: {o.get('crop_key')!r} is not a {prov.code} crop")
        if o.get("use") not in OVERRIDE_FIELDS.get(o.get("field"), ()):
            errors.append(f"source_overrides.{oid}: field/use must be one of {OVERRIDE_FIELDS}")
        if not o.get("reason"):
            errors.append(f"source_overrides.{oid}: a reason is required")

    outputs = raw.get("outputs", {})
    hist = outputs.get("historical_ratios", {})
    # [HIST-RATIOS] numerator / base return ratios per province (rows only where both crops exist)
    for code in hist.get("provinces", []):
        if code not in provinces:
            errors.append(f"outputs.historical_ratios.provinces: unknown province {code!r}")
    for g in hist.get("bases", []):
        for k in [g.get("base"), *g.get("numerators", [])]:
            if k not in crops:
                errors.append(f"outputs.historical_ratios.bases: {k!r} is not in the crop registry")
        if not g.get("numerators") or g.get("base") in g.get("numerators", []):
            errors.append(f"outputs.historical_ratios.bases: {g.get('base')!r} needs numerators other than itself")
    if not hist.get("bases"):
        errors.append("outputs.historical_ratios.bases: at least one base crop")

    # [PRAIRIE-WEIGHTED-RPA] StatCan acreage source + generated weights (src/pricing/statcan_weights.py)
    sc = raw.get("statcan_acreage", {})
    for key in sc.get("crops", {}):
        if key not in crops:
            errors.append(f"statcan_acreage.crops: {key!r} is not in the crop registry")
    for code in sc.get("provinces", {}):
        if code not in provinces:
            errors.append(f"statcan_acreage.provinces: {code!r} is not a province")
    for key, w in (raw.get("prairie_acreage_weights", {}).get("crops") or {}).items():
        shares = w.get("weights", {})
        if key not in crops or set(shares) - set(provinces):
            errors.append(f"prairie_acreage_weights.{key}: unknown crop or province")
        elif any(not 0 <= v <= 1 for v in shares.values()) or (any(shares.values()) and abs(sum(shares.values()) - 1) > 1e-4):
            errors.append(f"prairie_acreage_weights.{key}: weights must be in [0, 1] and sum to 1")

    if errors:
        raise RulesError(f"{Path(path).name} failed validation:\n  - " + "\n  - ".join(errors))
    return Rules(raw["schema_version"], (years[0], years[1]), baseline_year, rounding, float(pulse_lb),
                 {k: float(v) for k, v in lb_per_unit.items()}, crops, provinces, overrides, hist,
                 tuple(outputs.get("workbook_tabs", [])), raw)


def normalize_label(text: str) -> str:
    """Lower-case letters and digits only: 'Hard Red Spring Wheat**' -> 'hardredspringwheat'."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


RULES = load_rules()
PROVINCES: tuple[str, ...] = tuple(RULES.provinces)
YEARS = tuple(range(RULES.years[0], RULES.years[1] + 1))
BASELINE_YEAR = RULES.baseline_year
PULSE_LBS_PER_BU = RULES.pulse_lbs_per_bu


def province(code: str) -> ProvinceSpec:
    code = (code or "").strip().upper()
    if code not in RULES.provinces:
        raise ValueError(f"unknown province {code!r}; registered: {', '.join(PROVINCES)}")
    return RULES.provinces[code]


def crop(key: str) -> Crop:
    if key not in RULES.crops:
        raise KeyError(f"unknown crop_key {key!r}")
    return RULES.crops[key]


def lb_per_unit(unit: str) -> float:
    if unit not in RULES.lb_per_unit:
        raise ValueError(f"unknown quote unit {unit!r}; use one of {QUOTE_UNITS}")
    return RULES.lb_per_unit[unit]


def overrides_for(province_code: str, crop_key: str, field: str) -> list[dict]:
    return [o for o in RULES.source_overrides
            if o["province"] == province_code and o["crop_key"] == crop_key and o["field"] == field]


def source_pdfs(spec: ProvinceSpec) -> dict[int, Path]:
    """{year: pdf} for a province's guides within conventions.years (latest name wins per year)."""
    found: dict[int, Path] = {}
    for pdf in sorted(spec.input_dir.glob(spec.source["file_glob"])):
        m = PDF_YEAR_PATTERN.search(pdf.stem)
        if m and int(m.group(1)) in YEARS:
            found[int(m.group(1))] = pdf
    return found


def verify_environment(code: str) -> dict[int, Path]:
    spec = province(code)
    print(f"{spec.code} input folder : {spec.input_dir}")
    if not spec.input_dir.is_dir():
        raise FileNotFoundError(f"Input folder not found: {spec.input_dir}")
    for folder in (spec.intermediate_dir, spec.baseline_dir):
        folder.mkdir(parents=True, exist_ok=True)
    pdfs = source_pdfs(spec)
    print(f"Detected {len(pdfs)} PDF(s) for {YEARS[0]}-{YEARS[-1]}:")
    for year, path in sorted(pdfs.items()):
        print(f"  {year}  {path.name}  ({path.stat().st_size / 1_048_576:.1f} MB)")
    missing = [y for y in YEARS if y not in pdfs]
    if missing:
        print(f"  MISSING years: {', '.join(map(str, missing))}")
    return pdfs


if __name__ == "__main__":
    print(f"{RULES_PATH.name} v{RULES.schema_version}: valid "
          f"({len(RULES.crops)} crops, provinces {', '.join(PROVINCES)}, {len(RULES.source_overrides)} override(s))")
    for code in PROVINCES:
        verify_environment(code)
