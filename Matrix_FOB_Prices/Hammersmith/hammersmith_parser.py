"""Parse one Hammersmith weekly report into world FOB price rows and ocean freight rows.

Pure functions, no I/O. Input is a post dict from hammersmith_scraper ({"title", "published", "updated",
"link", "content"}); output is a ParseResult.

Where the data is
  * Yellow box (cell shading #FFFF99/#FFFFCC) = cash commodity FOBs; the largest yellow table is the price
    table (2019-2021 posts carry a second, 2-row yellow table of animal proteins).
  * Pink box (#FED6F5) = ocean freight. Paper indices (Baltic, Freightos) are dropped.
  * "ALL PRICES ARE FOR <near> <</ <far>" = the delivery window of every quote without its own months.
  If the shading is ever missing, the tables are found by content instead (rule-in by row labels).

Price quote grammar (USD/MT FOB; "m/t", "X" columns and "=7"-style junk are ignored)
  a<<b, a>>b, a -- b     FORWARD: a = near month, b = far month (arrow is not a direction)
    + "Feb"              far month override        + "Sep>>Nov"  explicit near and far months
  a/b                    BID_OFFER (mid used) for the whole window, flat
    + "spot"             SPOT: report month, plus a SPOT_PROXY row on the window's near month
    + "December"         that month only           + "July/Sep"  that window
  a + "flat/steady/to Mar"   FLAT from the window's near month to the named month
  "new crop"             kept, noted in crop_note   No prices / No info / N/A   NO_QUOTE

Month rows (one row per series x delivery month; agreed 2026-10-18)
  QUOTED        the month carries a published price
  INTERPOLATED  a month between a FORWARD near and far quote, linear (e.g. Dec between Nov and Jan)
  WINDOW        a flat bid/offer or flat price applied to every month of its window (OND / JFM style)
  SPOT_PROXY    a prompt spot quote (e.g. French wheat/barley/corn) copied to the window's NEAR month only;
                deferred months stay empty (N/A) unless Hammersmith publishes a forward curve (agreed 2026-10-19)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from bs4 import BeautifulSoup

import hs_config as hc

PRICE_COLUMNS = ["key", "report_date", "published_at", "series_id", "commodity", "origin_country",
                 "export_port", "protein_pct", "delivery_ym", "period_role", "price_type", "derivation",
                 "bid_usd_mt", "offer_usd_mt", "fob_usd_mt", "fob_usd_bu", "canada_peer", "quality_flag",
                 "crop_note", "raw_label", "raw_quote", "source_url"]
FREIGHT_COLUMNS = ["key", "report_date", "published_at", "route_id", "origin", "origin_country",
                   "destination", "destination_raw", "dest_port", "vessel_class", "vessel_size", "discharge_rate",
                   "route_note", "low_usd_mt", "high_usd_mt", "freight_usd_mt", "change_usd_mt", "status_note",
                   "quality_flag", "raw_label", "raw_rate", "source_url"]

MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10,
          "nov": 11, "dec": 12}
MONTH_RE = (r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
            r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?")
NO_QUOTE_RE = re.compile(r"^(?:usd\s*)?(?:no\s+prices?|no\s+info|n/?a|-+|)$", re.I)
_SERIES = [(re.compile(s["re"].replace(" ", "")), s) for s in hc.WORLD_SERIES]   # see match_series
_ORIGINS = sorted(hc.FREIGHT_ORIGINS.items(), key=lambda kv: -len(kv[0]))
_INDEX_RE = re.compile(hc.FREIGHT_INDEX_RE, re.I)


@dataclass
class ParseResult:
    status: str                       # REPORT | NOT_REPORT | PARSE_FAILED
    reason: str = ""
    report_date: date | None = None
    published_at: str = ""
    window: tuple[str, str] | None = None          # (near_ym, far_ym)
    prices: list[dict] = field(default_factory=list)
    freight: list[dict] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def norm(text: str) -> str:
    """Collapse whitespace (incl. &nbsp;) and unify dashes to '–'."""
    text = text.replace("\xa0", " ").replace("—", "–").replace("‒", "–")
    text = text.replace("�", "–")
    return re.sub(r"\s+", " ", text).strip()


def ym(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def month_num(token: str) -> int:
    return MONTHS[token.lower().rstrip(".")[:3]]


def add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


def months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + b.month - a.month


def next_month_on_or_after(month: int, start: date) -> date:
    """First 1st-of-month with this month number on or after `start` (resolves year-less months)."""
    for k in range(12):
        d = add_months(start, k)
        if d.month == month:
            return d
    raise AssertionError("unreachable")


def num(text: str) -> float:
    return float(text.replace(",", ""))


# --------------------------------------------------------------------------- #
# Dates and window
# --------------------------------------------------------------------------- #
def parse_date_text(text: str) -> date | None:
    """'October 17, 2026', 'Jan 07,2023', 'Sept 06, 2025', '11 15 2025' -> date."""
    m = re.search(MONTH_RE + r"\s+(\d{1,2})\s*,?\s*(\d{4})", text, re.I)
    if m:
        try:
            return date(int(m.group(3)), month_num(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    m = re.search(r"\b(\d{1,2})[ ./-](\d{1,2})[ ./-](\d{4})\b", text)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            return None
    return None


def report_date_of(body: str, title: str, published: date, issues: list[str]) -> date:
    """Body headline date, else title date, else the publish date. Must fall within 10 days before
    (or 1 day after) publication; resends published later than that keep their own report date."""
    m = re.search(r"GRAIN AND PROTEIN REPORT\s*[–-]*\s*(.{0,30})", body, re.I)    # 'USA'/'FEED GRAIN ...'
    for source, candidate in (("body", parse_date_text(m.group(1)) if m else None),
                              ("title", parse_date_text(title))):
        if candidate and published - timedelta(days=10) <= candidate <= published + timedelta(days=1):
            return candidate
        if candidate:
            issues.append(f"{source} date {candidate} is far from publish date {published}; ignored")
    issues.append("report date taken from publish date")
    return published


def parse_window(body: str, report_date: date) -> tuple[date, date] | None:
    """'ALL PRICES ARE FOR November 2026<<January 2027' / 'May/July 2019' -> (near, far) 1st-of-month."""
    # strict '<month> [year] <sep> <month> [year]' right after the phrase (commentary can follow it)
    # (year typos like 'January 20202' keep their first four digits)
    m = re.search(r"ALL PRICES ARE FOR\s*" + MONTH_RE + r"(?:\s*(\d{4})\d*)?\s*(?:<<|>>|/|–+|-+|to)\s*" + MONTH_RE +
                  r"(?:\s*(\d{4})\d*)?", body, re.I)
    if not m:
        return None
    m1, y1, m2, y2 = m.groups()
    y2 = int(y2) if y2 else (int(y1) if y1 else report_date.year)
    far = date(y2, month_num(m2), 1)
    near = date(int(y1), month_num(m1), 1) if y1 else date(y2 if month_num(m1) <= far.month else y2 - 1,
                                                          month_num(m1), 1)
    if not 0 < months_between(near, far) <= 6:
        return None
    return near, far


# --------------------------------------------------------------------------- #
# Table location
# --------------------------------------------------------------------------- #
def _shade(table) -> str:
    colours = re.findall(r"background\s*:\s*#([0-9a-f]{6})", str(table), re.I)
    if not colours:
        return ""
    return max(set(c.lower() for c in colours), key=lambda c: sum(x.lower() == c for x in colours))


def _rows(table) -> list[list[str]]:
    out = []
    for tr in table.find_all("tr"):
        cells = [norm(td.get_text(" ")) for td in tr.find_all(["td", "th"])]
        if any(cells):
            out.append(cells)
    return out


def locate_tables(soup) -> tuple[list[list[str]] | None, list[list[str]] | None, str]:
    """(price rows, freight rows, how) - largest yellow / pink table, else content signature."""
    tables = soup.find_all("table")
    yellow = [t for t in tables if _shade(t) in hc.HS_YELLOW]
    pink = [t for t in tables if _shade(t) in hc.HS_PINK]
    if yellow and pink:
        price = max(yellow, key=lambda t: len(t.find_all("tr")))
        freight = max(pink, key=lambda t: len(t.find_all("tr")))
        return _rows(price), _rows(freight), "colour"

    def score(t, pattern):
        return sum(bool(re.match(pattern, norm(tr.get_text(" ")), re.I)) for tr in t.find_all("tr"))
    by_price = sorted(tables, key=lambda t: score(t, r"(wheat|barley|corn|soybeans|soymeal),"), reverse=True)
    by_freight = sorted(tables, key=lambda t: score(t, r".*\b(panamax|handi\w*)\b"), reverse=True)
    price = by_price[0] if by_price and score(by_price[0], r"(wheat|barley|corn|soybeans|soymeal),") >= 10 else None
    freight = by_freight[0] if by_freight and score(by_freight[0], r".*\b(panamax|handi\w*)\b") >= 10 else None
    return (_rows(price) if price else None), (_rows(freight) if freight else None), "content"


# --------------------------------------------------------------------------- #
# Prices (yellow box)
# --------------------------------------------------------------------------- #
def match_series(label: str) -> dict | None:
    """Matched with all whitespace removed: Word sometimes splits words ('Soft Re d Winter', 'Wheat , USA')."""
    key = re.sub(r"\s+", "", norm(label).lower().replace("–", "-"))
    for rx, series in _SERIES:
        if rx.search(key):
            return series
    return None


def parse_quote(quote: str, label: str = "") -> dict:
    """One quote cell -> {"kind", "a", "b", "months", "spot", "flat", "new_crop"} (months: 3-letter tokens).
    kind: FORWARD | BID_OFFER | FLAT | NO_QUOTE | UNPARSED."""
    q = norm(quote)
    out = {"kind": "UNPARSED", "a": None, "b": None, "months": [], "spot": False, "flat": False,
           "new_crop": bool(re.search(r"new\b", q, re.I))}
    if NO_QUOTE_RE.match(q) or not re.search(r"\d", q):  # 'No prices', 'USD – no prices', 'No stock until May'
        out["kind"] = "NO_QUOTE"
        return out
    q = re.sub(r"(?i)\busd\b|m/t|\bmt\b", " ", q)
    q = re.sub(r"(?<=\d)=\d+", "", q)                     # 'USD 233/236=7 new crop'
    out["spot"] = bool(re.search(r"\bspot\b", q + " " + label, re.I))
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(<<|>>|–+|-{1,3}|/)\s*(\d[\d,]*(?:\.\d+)?)", q)
    if m:
        out["a"], out["b"] = num(m.group(1)), num(m.group(3))
        out["kind"] = "BID_OFFER" if m.group(2) == "/" else "FORWARD"
        rest = q[m.end():]
    else:
        m = re.search(r"(\d[\d,]*(?:\.\d+)?)", q)
        if not m:
            return out
        out["a"], out["kind"] = num(m.group(1)), "FLAT"
        rest = q[m.end():]
        out["flat"] = True
    out["months"] = [t[:3].lower() for t in re.findall(r"\b" + MONTH_RE + r"(?![a-z])", rest, re.I)]
    return out


def _price_flag(value: float | None) -> str:
    lo, hi = hc.HS_PRICE_RANGE
    return "OUTLIER" if value is not None and not lo <= value <= hi else "OK"


def expand_quote(pq: dict, near: date, far: date, report_month: date) -> list[dict]:
    """Quote -> month rows [{month, price, bid, offer, role, price_type, derivation}]."""
    def resolve(tokens: list[str], start: date) -> list[date]:
        out, cur = [], start
        for t in tokens:
            cur = next_month_on_or_after(MONTHS[t], cur)
            out.append(cur)
        return out

    rows = []
    kind, a, b, toks = pq["kind"], pq["a"], pq["b"], pq["months"]
    if kind == "FORWARD":
        if len(toks) >= 2:
            m_near, m_far = resolve(toks[:1] + toks[-1:], near)
        elif len(toks) == 1:
            m_near, m_far = near, resolve(toks, add_months(near, 1))[0]
        else:
            m_near, m_far = near, far
        n = max(months_between(m_near, m_far), 1)
        for i in range(n + 1):
            price = round(a + (b - a) * i / n, 2)
            role = "NEAR" if i == 0 else "FAR" if i == n else "MID"
            rows.append({"month": add_months(m_near, i), "price": price, "bid": None, "offer": None, "role": role,
                         "price_type": "FORWARD", "derivation": "QUOTED" if role != "MID" else "INTERPOLATED"})
        return rows
    if kind == "BID_OFFER" and pq["spot"]:
        spot = {"month": report_month, "price": round((a + b) / 2, 2), "bid": a, "offer": b, "role": "SPOT",
                "price_type": "SPOT", "derivation": "QUOTED"}
        if near <= report_month:                          # window starts this month: the spot row covers it
            return [spot]
        return [spot, {**spot, "month": near, "role": "NEAR", "derivation": "SPOT_PROXY"}]
    if kind in ("BID_OFFER", "FLAT"):
        price = round((a + b) / 2, 2) if kind == "BID_OFFER" else a
        bid, offer = (a, b) if kind == "BID_OFFER" else (None, None)
        if kind == "FLAT" and toks:                       # '205 flat to Mar'
            months = [near] + resolve(toks[-1:], near)
        elif len(toks) == 1:                              # '280/285 December' - that month only
            m = resolve(toks, near)[0]
            return [{"month": m, "price": price, "bid": bid, "offer": offer, "role": "NEAR",
                     "price_type": kind, "derivation": "QUOTED"}]
        elif len(toks) >= 2:                              # '184/186 July/Sep New'
            months = resolve(toks[:1] + toks[-1:], near)
        else:
            months = [near, far]
        n = months_between(months[0], months[-1])
        return [{"month": add_months(months[0], i), "price": price, "bid": bid, "offer": offer,
                 "role": "WINDOW", "price_type": kind, "derivation": "WINDOW"} for i in range(n + 1)]
    return rows


def parse_prices(rows: list[list[str]], near: date, far: date, report_month: date, base: dict,
                 issues: list[str]) -> list[dict]:
    out = []
    for cells in rows:
        label = cells[0]
        if not re.match(r"[a-z]", label, re.I) or len(cells) < 2:
            continue
        quote = next((c for c in cells[1:] if c and c.lower() != "x"), "")
        series = match_series(label)
        if series is None:
            if not re.match(r"(wheat|barley|corn|sorghum|soy|ddgs)", label, re.I):
                continue                                  # section header / spacer text, not a price row
            issues.append(f"UNMAPPED price label: {label!r}")
        pq = parse_quote(quote, label)
        if pq["kind"] == "UNPARSED":
            issues.append(f"UNPARSED quote for {label!r}: {quote!r}")
        protein = re.search(r"(\d+(?:\.\d+)?)\s*(?:%|pro)", label, re.I)
        common = {
            **base, "series_id": series["id"] if series else "UNMAPPED",
            "commodity": series["commodity"] if series else label.split(",")[0].strip().title(),
            "origin_country": series["origin"] if series else "", "export_port": series["port"] if series else "",
            "protein_pct": float(protein.group(1)) if protein else None,
            "canada_peer": series["peer"] if series else "", "crop_note": "new crop" if pq["new_crop"] else "",
            "raw_label": label, "raw_quote": quote,
        }
        bu = series["bu"] if series else None
        months = expand_quote(pq, near, far, report_month) if pq["kind"] not in ("NO_QUOTE", "UNPARSED") else []
        if not months:
            flag = "UNMAPPED" if not series else "NO_QUOTE"
            months = [{"month": near, "price": None, "bid": None, "offer": None, "role": "NEAR",
                       "price_type": pq["kind"], "derivation": "QUOTED"}]
        else:
            flag = "UNMAPPED" if not series else None
        for r in months:
            out.append({
                **common, "delivery_ym": ym(r["month"]), "period_role": r["role"], "price_type": r["price_type"],
                "derivation": r["derivation"], "bid_usd_mt": r["bid"], "offer_usd_mt": r["offer"],
                "fob_usd_mt": r["price"],
                "fob_usd_bu": round(r["price"] / bu, 4) if bu and r["price"] is not None else None,
                "quality_flag": flag or _price_flag(r["price"]),
            })
    for r in out:
        r["key"] = f"{r['report_date']}|{r['series_id']}|{r['delivery_ym']}"
    seen: dict[str, int] = {}
    for r in out:                                        # same series twice in one report -> keep both, suffix
        seen[r["key"]] = seen.get(r["key"], 0) + 1
        if seen[r["key"]] > 1:
            issues.append(f"duplicate price key {r['key']}")
            r["key"] += f"#{seen[r['key']]}"
    return out


# --------------------------------------------------------------------------- #
# Freight (pink box)
# --------------------------------------------------------------------------- #
def parse_rate(cell: str) -> dict | None:
    """'$46.00', '$61/62.00', '$ 2 9 / 3 0 .00', '$95 Russia', 'Ukraine $56' -> {low, high, country}."""
    c = norm(cell)
    country = None
    m = re.match(r"^(russia|ukraine|romania)\s+(\$.*)$", c, re.I) or re.match(r"^(\$.*?)\s+(russia|ukraine|romania)$",
                                                                              c, re.I)
    if m:
        a, b = m.groups()
        country, c = (a, b) if a.lower() in ("russia", "ukraine", "romania") else (b, a)
        country = country.title()
    if not re.match(r"^\$[\d\s.,/]+$", c):
        return None
    c = re.sub(r"\s+", "", c)[1:]
    parts = c.split("/")
    try:
        if len(parts) == 1:
            low = high = num(parts[0])
        else:
            hi_txt = parts[1]
            low, high = num(parts[0]), num(hi_txt)
    except ValueError:
        return None
    return {"low": low, "high": high, "country": country}


def parse_change(cell: str) -> float | None:
    c = norm(cell).lower()
    if c in ("steady", "stady", "unchanged"):
        return 0.0
    m = re.match(r"^(up|down)\s*\$?\s*([\d.]+)", c)
    if m:
        return num(m.group(2)) * (1 if m.group(1) == "up" else -1)
    return None


def _prefix_end(text: str, prefix: str) -> int | None:
    """End index of `prefix` at the start of `text`, ignoring spaces on both sides ('U S Gulf', 'US Gu lf')."""
    i = 0
    for ch in prefix.replace(" ", ""):
        while i < len(text) and text[i] == " ":
            i += 1
        if i >= len(text) or text[i].lower() != ch:
            return None
        i += 1
    return i


def parse_route(label: str) -> dict:
    """'US Gulf Algeria/Tunisia: Handi' -> origin, destinations, vessel class/size, discharge, port, notes."""
    text = re.sub(r"\s*/\s*", "/", norm(label))            # 'France /Germany', 'Algeria /Tunisia'
    origin, rest = "", text
    for prefix, name in _ORIGINS:
        end = _prefix_end(text, prefix)
        if end is not None:
            origin, rest = name, text[end:]
            break
    rest = re.sub(r"^\s*(?:to\b)?\s*", "", rest, flags=re.I)
    # separators: ':', ',', en dash, '--', spaced '-'; keep '40-55,000' and 'top-off' intact
    rest = re.sub(r"\s*(?::|–+-*|-{2,}|\s-\s|(?<=\))-|,(?!\d{3}))\s*", " | ", rest).strip(" |")
    parts = [p.strip() for p in rest.split(" | ") if p.strip()]
    dest_txt = parts[0] if parts else ""
    spec_parts = parts[1:]
    m = re.match(r"^(.*?)\s+(\d.*)$", dest_txt)            # 'South Africa 40,000 m/t' (no separator)
    if m and not re.search(r"\d", m.group(1)):
        dest_txt, spec_parts = m.group(1), [m.group(2)] + spec_parts
    dest_port = ""
    pm = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", dest_txt)   # 'Turkey (Izmir)'
    if pm:
        dest_txt, dest_port = pm.group(1), pm.group(2)
    spec = " ".join(spec_parts)
    vessel_class = ""
    for token, cls in hc.VESSEL_CLASSES.items():
        if re.search(rf"\b{token}\b", spec, re.I):
            vessel_class = cls
            break
    size = re.search(r"(\d+\s*[/-]\s*)?\d+\s*,\s*\d{3}(?!\s*(?:dis))|\b\d+k\b", re.sub(r"\([^)]*\)", "", spec))
    disch = re.search(r"\(\s*([\d,]+k?)\s*dis\w*\s*\)", spec, re.I)
    notes = [n for n in ("via panama", "with top-off", "no top off", "round trip")
             if n in spec.lower().replace("top off", "top off")]
    for p in spec_parts:                                   # 'Jeddah', 'Izmir' - a named port segment
        bare = re.sub(r"\([^)]*\)", "", p).strip()
        if (bare and not re.search(r"\d", bare) and not dest_port and bare.lower() not in hc.VESSEL_CLASSES
                and not any(n in bare.lower() for n in ("panama", "top", "trip", "m/t", "coaster"))):
            dest_port = bare
    raw_dests = [d.strip() for d in dest_txt.split("/") if d.strip()]
    dests = [hc.DESTINATION_ALIASES.get(d.lower(), d) for d in raw_dests]
    return {"origin": origin, "destinations": dests, "destination_raw": dest_txt, "dest_port": dest_port,
            "vessel_class": vessel_class,
            "vessel_size": re.sub(r"\s+", "", size.group(0)) if size else "",
            "discharge_rate": re.sub(r"\s+", "", disch.group(1)) if disch else "",
            "route_note": "; ".join(notes)}


def _slug(*parts: str) -> str:
    return "_".join(re.sub(r"[^A-Z0-9]+", "", p.upper()) for p in parts if p)


def parse_freight(rows: list[list[str]], base: dict, issues: list[str]) -> list[dict]:
    out = []
    lo, hi = hc.HS_FREIGHT_RANGE
    for cells in rows:
        label = cells[0]
        if _INDEX_RE.search(label) or not re.match(r"[a-z]", label, re.I):
            continue                                      # paper indices and spacer rows are not freight
        values = [c for c in cells[1:] if c and c.lower() != "x"]
        rates = [(c, parse_rate(c)) for c in values]
        priced = [(c, r) for c, r in rates if r and r["high"] > 0]          # '$00.00' + 'Closed' = no quote
        status = next((c for c, r in rates if not (r and r["high"] > 0) and parse_change(c) is None
                       and re.match(r"(?i)(adjusted|corrected|new|closed|no\s+prices?|no\s+info|n/a)$", c)), "")
        if priced and status.lower() in ("n/a", "no info", "no prices", "no price"):
            status = ""                                   # e.g. '$95 Russia | N/A' - Ukraine leg not quoted
        change = next((parse_change(c) for c in values if parse_change(c) is not None), None)
        if not priced and not status:
            continue                                      # 'Black Sea freight' section headers
        route = parse_route(label)
        if not route["origin"]:
            issues.append(f"UNMAPPED freight origin: {label!r}")
        if not route["destinations"]:
            issues.append(f"no destination in freight label: {label!r}")
            continue
        entries = priced or [("", None)]
        for raw_rate, r in entries:
            for dest in route["destinations"]:
                if r:
                    mid = round((r["low"] + r["high"]) / 2, 2)
                    flag = "OK" if lo <= mid <= hi else "OUTLIER"
                else:
                    mid, flag = None, "NO_QUOTE"
                if not route["origin"]:
                    flag = "UNMAPPED"
                country = r["country"] if r else None
                out.append({
                    **base, "route_id": _slug(route["origin"], country or "", dest,
                                              route["vessel_class"] or route["vessel_size"]),
                    "origin": route["origin"], "origin_country": country or "", "destination": dest,
                    "destination_raw": route["destination_raw"], "dest_port": route["dest_port"],
                    "vessel_class": route["vessel_class"], "vessel_size": route["vessel_size"],
                    "discharge_rate": route["discharge_rate"], "route_note": route["route_note"],
                    "low_usd_mt": r["low"] if r else None, "high_usd_mt": r["high"] if r else None,
                    "freight_usd_mt": mid, "change_usd_mt": change if len(entries) == 1 else None,
                    "status_note": status, "quality_flag": flag, "raw_label": label, "raw_rate": raw_rate,
                })
    seen: dict[str, int] = {}
    for r in out:
        k = f"{r['report_date']}|{r['route_id']}"
        seen[k] = seen.get(k, 0) + 1
        r["key"] = k if seen[k] == 1 else f"{k}#{seen[k]}"
    return out


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def parse_post(post: dict) -> ParseResult:
    published = datetime.fromisoformat(post["published"])
    res = ParseResult(status="NOT_REPORT", published_at=post["published"])
    soup = BeautifulSoup(post.get("content") or "", "lxml")
    body = norm(soup.get_text(" "))
    price_rows, freight_rows, how = locate_tables(soup)
    has_header = bool(re.search(r"ALL PRICES ARE FOR", body, re.I))
    if not price_rows and not has_header:
        res.reason = "no price table or price window - not a weekly report"
        return res
    res.report_date = report_date_of(body, post.get("title", ""), published.date(), res.issues)
    window = parse_window(body, res.report_date)
    if not price_rows or not window:
        res.status = "PARSE_FAILED"
        res.reason = "price table not found" if not price_rows else "price window header not parsed"
        return res
    if how != "colour":
        res.issues.append("tables located by content (cell shading not found)")
    near, far = window
    res.window = (ym(near), ym(far))
    report_month = res.report_date.replace(day=1)
    base = {"report_date": res.report_date.isoformat(), "published_at": post["published"],
            "source_url": post.get("link", "")}
    res.prices = parse_prices(price_rows, near, far, report_month, base, res.issues)
    res.freight = parse_freight(freight_rows or [], base, res.issues)
    mapped = {r["series_id"] for r in res.prices if r["series_id"] != "UNMAPPED"}
    if len(mapped) < 10:
        res.status, res.reason = "PARSE_FAILED", f"only {len(mapped)} price series recognised"
        return res
    if not res.freight:
        res.issues.append("no freight rows parsed")
    res.status = "REPORT"
    return res
