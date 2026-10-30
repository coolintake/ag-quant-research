"""Unified crop translation: raw provincial crop descriptions -> canonical crop_key + trade label.

[CROP-MAP] Resolution order, per province (docs/AGENT_RULES.json provinces.<code>.crops):
    1. exact match on a source alias (case, spacing, punctuation and '*' footnotes ignored)
    2. first matching source pattern (regex on the lower-cased text), in the rules' crop order
Anything else is None: an unmapped crop stays out of every baseline rather than being guessed.
"""

from __future__ import annotations

import re
from collections import Counter
from functools import lru_cache

from ..config import crop, normalize_label, province


@lru_cache(maxsize=None)
def _alias_index(code: str) -> dict[str, str]:
    return {normalize_label(a): key for key, pc in province(code).crops.items() for a in pc.source_aliases}


def resolve(code: str, raw_label: str | None) -> str | None:
    """crop_key for a raw label as printed by the province's guide, or None."""
    if not raw_label:
        return None
    key = _alias_index(code).get(normalize_label(raw_label))
    if key:
        return key
    text = raw_label.lower().replace("*", "").strip()
    for key, pc in province(code).crops.items():
        if any(re.search(p, text) for p in pc.source_patterns):
            return key
    return None


def resolve_votes(code: str, labels: list[str]) -> str | None:
    """Majority crop_key over several readings of one label (OCR variants); ties -> first seen."""
    keys = [k for k in (resolve(code, lab) for lab in labels) if k]
    if not keys:
        return None
    counts = Counter(keys)
    top = max(counts.values())
    return next(k for k in keys if counts[k] == top)


def trade_label(key: str) -> str:
    return crop(key).trade_label


def translate(code: str, raw_label: str) -> tuple[str | None, str | None]:
    key = resolve(code, raw_label)
    return key, (trade_label(key) if key else None)
