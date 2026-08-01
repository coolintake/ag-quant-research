"""
mappings.py — Centralised label dictionaries for the Wheat Arbitrage Analysis System.

All Origin, Commodity, Delivery_Window, and zone-selection logic lives here.
To rename a label, add a zone, or change a representative zone, edit only
this file — no extractor or transformer code needs to change.
"""

# ════════════════════════════════════════════════════════════════════════════
#  USW
# ════════════════════════════════════════════════════════════════════════════

# ── Region: raw workbook cell pairs → clean Origin label ─────────────────────
# ── USW region → clean Origin label ─────────────────────────────────────────
#
# USW_REGION_ALIASES maps any reasonable way someone might write a region
# name in col 0 of Table_FOB → the canonical clean Origin label.
# Keys are stored in UPPER CASE; the resolver calls .upper().strip() before
# lookup so capitalisation and leading/trailing spaces never matter.
#
# To add a new alias, just add a line here — no code changes needed.
#
USW_REGION_ALIASES: dict[str, str] = {
    # ── US Gulf ───────────────────────────────────────────────────────────
    "GULF OF MEXICO":     "US Gulf",
    "GULF OF MEXICO (US GULF)": "US Gulf",
    "US GULF":            "US Gulf",
    "U.S. GULF":          "US Gulf",
    "GULF":               "US Gulf",
    "GULF OF":            "US Gulf",   # split-cell first half
    "MEXICO":             "US Gulf",   # split-cell second half
    "GM":                 "US Gulf",
    "GOM":                "US Gulf",
    "G.O.M.":             "US Gulf",
    "USGULF":             "US Gulf",
    "US GULF COAST":      "US Gulf",
    "GULF COAST":         "US Gulf",
    "NEW ORLEANS":        "US Gulf",
    "NOLA":               "US Gulf",

    # ── US PNW ────────────────────────────────────────────────────────────
    "PACIFIC N.WEST":     "US PNW",
    "PACIFIC N. WEST":    "US PNW",
    "PACIFIC NORTHWEST":  "US PNW",
    "PACIFIC NORTH WEST": "US PNW",
    "PACIFIC NW":         "US PNW",
    "PACIFIC N/W":        "US PNW",
    "US PNW":             "US PNW",
    "U.S. PNW":           "US PNW",
    "PNW":                "US PNW",
    "P.N.W.":             "US PNW",
    "PACIFIC":            "US PNW",   # split-cell first half
    "N.WEST":             "US PNW",   # split-cell second half
    "N. WEST":            "US PNW",
    "NORTHWEST":          "US PNW",
    "NORTH WEST":         "US PNW",
    "SEATTLE":            "US PNW",
    "PORTLAND":           "US PNW",
    "COLUMBIA RIVER":     "US PNW",

    # ── US Great Lakes ────────────────────────────────────────────────────
    "GREAT LAKES":        "US GL",
    "GREAT LAKES (US)": "US GL",
    "US GREAT LAKES":     "US GL",
    "U.S. GREAT LAKES":   "US GL",
    "GL":                 "US GL",
    "G.L.":               "US GL",
    "LAKES":              "US GL",
    "GREAT LAKES REGION": "US GL",
    "DULUTH":             "US GL",
    "CHICAGO":            "US GL",
    "TOLEDO":             "US GL",
}

# Legacy tuple-key map kept for any code that still calls _resolve_region
# with a (part_a, part_b) pair. Not used by Table_FOB extractor.
USW_REGION_MAP: dict[tuple[str, str], str] = {
    ("Great Lakes",    ""): "US GL",
    ("Gulf of Mexico", ""): "US Gulf",
    ("Pacific N.West", ""): "US PNW",
    ("Gulf of",  "Mexico"): "US Gulf",
    ("Pacific",  "N.West"): "US PNW",
    ("Gulf of",  ""):       "US Gulf",
    ("Mexico",   ""):       "US Gulf",
    ("Pacific",  ""):       "US PNW",
    ("N.West",   ""):       "US PNW",
}

# ── Class prefix → canonical class abbreviation ──────────────────────────────
USW_CLASS_LABEL: dict[str, str] = {
    "HRS": "HRS",
    "HRW": "HRW",
    "SRW": "SRW",
    "SW":  "SW",
    "WW":  "WW",
    "HW":  "HW",
}

# ── Grade strings with no numeric protein → fixed label ──────────────────────
USW_SPECIAL_GRADES: dict[str, str] = {
    "HRW Ord":        "HRW Ord",
    "SRW":            "SRW",
    "SW Unspecified": "SW Unspecified",
    "WW 10% Club":    "WW 10% Club",
    "WW 20% Club":    "WW 20% Club",
}

# ── Delivery window: raw header label → clean calendar label ─────────────────
USW_WINDOW_MAP: dict[str, str] = {
    "JUN (N26)": "Jun-26",
    "JUL (N26)": "Jul-26",
    "AUG (U26)": "Aug-26",
    "SEP (U26)": "Sep-26",
    "OCT (Z26)": "Oct-26",
    "NOV (Z26)": "Nov-26",
    "DEC (Z26)": "Dec-26",
}


# ════════════════════════════════════════════════════════════════════════════
#  PDQ (Canada)
# ════════════════════════════════════════════════════════════════════════════

# ── Zone → clean Origin label ─────────────────────────────────────────────────
PDQ_ZONE_ORIGIN_MAP: dict[str, str] = {
    "PEACE":   "Canada – VC",
    "N ALTA":  "Canada – VC",
    "S ALTA":  "Canada – VC",
    "NW SASK": "Canada – VC",
    "SW SASK": "Canada – VC",
    "NE SASK": "Canada – VC",
    "SE SASK": "Canada – VC",
    "W MAN":   "Canada – STL",
    "E MAN":   "Canada – STL",
}

# ── Representative zone per commodity class for FOB VC summary ───────────────
#
# When multiple zones are present in the PDQ download, the transformer
# selects only the designated zone per commodity for the FOB summary.
# All zones are still stored in raw_pdq for auditing.
#
# Business rules:
#   CWRS, CPSR → NE SASK  (largest liquid market for spring wheat basis)
#   CWAD       → SW SASK  (durum heartland; bids more consistently quoted)
#   Default    → NE SASK  (fallback for any class not listed here)
#
PDQ_REPRESENTATIVE_ZONE: dict[str, str] = {
    "CWRS": "NE SASK",
    "CPSR": "NE SASK",
    "CWAD": "SW SASK",
    "CPSW": "NE SASK",
    "CWES": "NE SASK",
}
PDQ_REPRESENTATIVE_ZONE_DEFAULT = "NE SASK"

# ── Class code → canonical class abbreviation ─────────────────────────────────
PDQ_CLASS_LABEL: dict[str, str] = {
    "CWRS": "CWRS",
    "CPSR": "CPSR",
    "CWAD": "CWAD",
    "CPSW": "CPSW",
    "CWES": "CWES",
}

# ── Delivery window: raw PDQ MONTH column → clean calendar label ──────────────
PDQ_WINDOW_MAP: dict[str, str] = {
    "JUN '26": "Jun-26",
    "JUL '26": "Jul-26",
    "AUG '26": "Aug-26",
    "SEP '26": "Sep-26",
    "OCT '26": "Oct-26",
    "NOV '26": "Nov-26",
    "DEC '26": "Dec-26",
}


# ════════════════════════════════════════════════════════════════════════════
#  Hammersmith (International)
# ════════════════════════════════════════════════════════════════════════════

# ── FOB origin → commodity metadata ──────────────────────────────────────────
#
# commodity:      clean label used in wheat_summary
# freight_basis:  load port / vessel-size context for future C&F calculation
# exclude_fob:    True = row is excluded from the FOB summary because a more
#                 granular authoritative source already covers this origin.
#                 The row is still stored in raw_hammer_fob for reference.
#
# Exclusion rationale:
#   USA Gulf wheat (SRW, HRW) → USW report covers these in full detail
#   (7 delivery months × multiple protein levels × Gulf + PNW).
#   Keeping Hammersmith US Gulf wheat in the summary would create duplicate
#   and less-granular entries alongside the USW data.
#
HAMMER_COMMODITY_MAP: dict[str, dict] = {
    "Ukraine 11.5 pro, 30,000+ MT": {
        "commodity":     "Wheat Ukraine 11.5%",
        "freight_basis": "Black Sea",
        "exclude_fob":   False,
    },
    "Russia 12.5 pro, 30,000+ MT": {
        "commodity":     "Wheat Russia 12.5%",
        "freight_basis": "Black Sea",
        "exclude_fob":   False,
    },
    "Romania 12.5 pro": {
        "commodity":     "Wheat Romania 12.5%",
        "freight_basis": "Black Sea",
        "exclude_fob":   False,
    },
    "Superior, France": {
        "commodity":     "Wheat France Superior",
        "freight_basis": "Atlantic",
        "exclude_fob":   False,
    },
    "USA Hard Red Winter 11 protein, US Gulf": {
        "commodity":     "Wheat USA HRW 11.0%",
        "freight_basis": "US Gulf",
        "exclude_fob":   True,   # covered by USW with full protein curve
    },
    "USA Soft Red Winter, US Gulf": {
        "commodity":     "Wheat USA SRW",
        "freight_basis": "US Gulf",
        "exclude_fob":   True,   # covered by USW
    },
    "milling, 12.0%, Argentina, Upriver": {
        "commodity":     "Wheat Argentina 12.0%",
        "freight_basis": "Upriver",
        "exclude_fob":   False,
    },
    "feed, Black Sea": {
        "commodity":     "Wheat Feed Black Sea",
        "freight_basis": "Black Sea",
        "exclude_fob":   False,
    },
}


# ════════════════════════════════════════════════════════════════════════════
#  ARBITRAGE MATRIX — tier definitions and display configuration
# ════════════════════════════════════════════════════════════════════════════

# ── Commodity inclusion filter ────────────────────────────────────────────────
# Only commodities listed here appear in the arbitrage matrix.
# Key   = exact Commodity string from wheat_summary
# Value = dict with:
#   tier         : "High" | "Mid" | "Low"
#   display_name : short label shown in the matrix (max ~22 chars)
#   origin_short : short origin label for the matrix Origin column
#   suppress     : True = never show in matrix (DHV grades, niche specs)
#
# To add a grade to the matrix, add an entry here.
# To suppress a grade, set suppress=True (keeps it in DB, hides from report).

MATRIX_COMMODITIES: dict[str, dict] = {

    # ── HIGH PROTEIN ─────────────────────────────────────────────────────────
    "CWRS 13.5%": {
        "tier":         "High",
        "display_name": "CWRS 13.5%",
        "origin_short": "Canada (VC)",
        "suppress":     False,
    },
    "CWAD 13.0%": {
        "tier":         "High",
        "display_name": "CWAD 13.0%",
        "origin_short": "Canada (VC)",
        "suppress":     True,    # Durum — excluded from ex-Durum wheat matrix
    },
    "HRS 13.0%": {
        "tier":         "High",
        "display_name": "HRS 13.0%",
        "origin_short": "US PNW",
        "suppress":     False,
    },
    "HRS 13.5%": {
        "tier":         "High",
        "display_name": "HRS 13.5%",
        "origin_short": "US PNW",
        "suppress":     True,    # suppress for now; enable when needed
    },
    "HRS 14.0%": {
        "tier":         "High",
        "display_name": "HRS 14.0%",
        "origin_short": "US PNW",
        "suppress":     True,
    },
    "HRS 14.5%": {
        "tier":         "High",
        "display_name": "HRS 14.5%",
        "origin_short": "US PNW",
        "suppress":     True,
    },
    "HRS 14.0% (50 DHV)": {
        "tier":         "High",
        "display_name": "HRS 14.0% (50 DHV)",
        "origin_short": "US PNW",
        "suppress":     True,    # DHV — suppress until bleaching year
    },

    # ── MID PROTEIN ──────────────────────────────────────────────────────────
    "CPSR 11.5%": {
        "tier":         "Mid",
        "display_name": "CPSR 11.5%",
        "origin_short": "Canada (VC)",
        "suppress":     False,
    },
    "HRW 11.0%": {
        "tier":         "Mid",
        "display_name": "HRW 11.0%",
        "origin_short": "US PNW",
        "suppress":     False,
    },
    "HRW 11.5%": {
        "tier":         "Mid",
        "display_name": "HRW 11.5%",
        "origin_short": "US PNW",
        "suppress":     True,
    },
    "HRW 12.0%": {
        "tier":         "Mid",
        "display_name": "HRW 12.0%",
        "origin_short": "US PNW",
        "suppress":     True,
    },
    "HRW Ord": {
        "tier":         "Mid",
        "display_name": "HRW Ord",
        "origin_short": "US PNW",
        "suppress":     True,
    },
    "Wheat Ukraine 11.5%": {
        "tier":         "Mid",
        "display_name": "Ukraine 11.5%",
        "origin_short": "Ukraine",
        "suppress":     False,
    },
    "Wheat Russia 12.5%": {
        "tier":         "Mid",
        "display_name": "Russia 12.5%",
        "origin_short": "Russia",
        "suppress":     False,
    },
    "Wheat Romania 12.5%": {
        "tier":         "Mid",
        "display_name": "Romania 12.5%",
        "origin_short": "Romania",
        "suppress":     False,
    },
    "Wheat Argentina 12.0%": {
        "tier":         "Mid",
        "display_name": "Argentina 12.0%",
        "origin_short": "Argentina",
        "suppress":     False,
    },

    # ── LOW PROTEIN ───────────────────────────────────────────────────────────
    "Wheat Feed Black Sea": {
        "tier":         "Low",
        "display_name": "Feed Wheat",
        "origin_short": "Black Sea",
        "suppress":     False,
    },
    "Wheat France Superior": {
        "tier":         "Low",
        "display_name": "Superior (Soft)",
        "origin_short": "France",
        "suppress":     False,
    },
    "SRW": {
        "tier":         "Low",
        "display_name": "SRW",
        "origin_short": "US Gulf",
        "suppress":     False,
    },
    "SW 9.5% Min": {
        "tier":         "Low",
        "display_name": "SW 9.5% Min",
        "origin_short": "US PNW",
        "suppress":     True,
    },
    "SW 9.5% Max": {
        "tier":         "Low",
        "display_name": "SW 9.5% Max",
        "origin_short": "US PNW",
        "suppress":     True,
    },
    "SW 10.5% Max": {
        "tier":         "Low",
        "display_name": "SW 10.5% Max",
        "origin_short": "US PNW",
        "suppress":     True,
    },
    "SW Unspecified": {
        "tier":         "Low",
        "display_name": "SW Unspecified",
        "origin_short": "US PNW",
        "suppress":     True,
    },
    "WW 10% Club": {
        "tier":         "Low",
        "display_name": "WW 10% Club",
        "origin_short": "US PNW",
        "suppress":     True,
    },
    "WW 20% Club": {
        "tier":         "Low",
        "display_name": "WW 20% Club",
        "origin_short": "US PNW",
        "suppress":     True,
    },
}

# ── Tier baselines (used for spread calculation) ──────────────────────────────
# Spread = competitor Jul-26 price minus baseline Jul-26 price.
# Negative spread = competitor is cheaper than Canadian baseline (threat).
# Positive spread = competitor is more expensive (Canadian is competitive).
# Low protein has no baseline — spread column is omitted for that tier.

MATRIX_BASELINES: dict[str, dict | None] = {
    "High": {"commodity": "CWRS 13.5%",  "origin": "Canada – VC"},
    "Mid":  {"commodity": "CPSR 11.5%",  "origin": "Canada – VC"},
    "Low":  None,   # no Canadian baseline in low-protein tier
}

# ── Display windows ────────────────────────────────────────────────────────────
# Columns shown in the matrix, in order.
# "Spot" maps to the Jun-26 column (treated as prompt) with a footnote marker.
MATRIX_WINDOWS = ["Jun-26", "Jul-26", "Aug-26"]
SPOT_WINDOW    = "Spot"   # shown in Jun-26 col with * footnote
