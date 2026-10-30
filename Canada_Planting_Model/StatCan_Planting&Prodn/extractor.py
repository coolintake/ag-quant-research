"""Extract: select a topic's release tabs from the workbook and read their raw cell values.

Selection is driven entirely by ``config.TopicSpec`` (marker block, release-name pattern, month and
year filters), so enabling production tabs later needs no change here.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from openpyxl import load_workbook

import config
from transformer import Release, parse_release

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RawSheet:
    name: str
    release: Release
    topic: str
    rows: list[tuple]


@dataclass(frozen=True)
class SheetDecision:
    name: str
    selected: bool
    reason: str


def _block(sheetnames: list[str], spec: config.TopicSpec) -> tuple[list[str], list[str]]:
    """Sheets strictly between the topic's markers. Returns (block, problems)."""
    problems = []
    start, end = 0, len(sheetnames)
    if spec.start_marker is not None:
        if spec.start_marker in sheetnames:
            start = sheetnames.index(spec.start_marker) + 1
        else:
            problems.append(f"start marker {spec.start_marker!r} not found")
    if spec.end_marker is not None:
        if spec.end_marker in sheetnames:
            end = sheetnames.index(spec.end_marker)
        else:
            problems.append(f"end marker {spec.end_marker!r} not found")
    return sheetnames[start:end], problems


def select_sheets(sheetnames: list[str], spec: config.TopicSpec) -> list[SheetDecision]:
    """Decide, for every sheet in the topic's marker block, whether it is a target release tab."""
    block, problems = _block(sheetnames, spec)
    if problems:
        raise ValueError(f"{spec.name}: " + "; ".join(problems))
    decisions = []
    for name in block:
        rel = parse_release(name)
        if rel is None:
            decisions.append(SheetDecision(name, False, "name is not a release (MonthYYYY / YYYYMonth)"))
        elif spec.release_months and rel.month not in spec.release_months:
            decisions.append(SheetDecision(name, False, f"release month {rel.month_name} not in topic filter"))
        elif spec.first_release_year and rel.year < spec.first_release_year:
            decisions.append(SheetDecision(name, False, f"release year {rel.year} before {spec.first_release_year}"))
        elif spec.last_release_year and rel.year > spec.last_release_year:
            decisions.append(SheetDecision(name, False, f"release year {rel.year} after {spec.last_release_year}"))
        else:
            decisions.append(SheetDecision(name, True, f"{rel.code}"))
    codes = [parse_release(d.name).code for d in decisions if d.selected]
    dupes = {c for c in codes if codes.count(c) > 1}
    if dupes:
        raise ValueError(f"{spec.name}: more than one tab for release(s) {sorted(dupes)}")
    return decisions


def _open(path: Path):
    """Open read-only. If Excel holds an exclusive lock, read a temporary copy instead."""
    try:
        return load_workbook(path, read_only=True, data_only=True), None
    except PermissionError:
        tmp = Path(tempfile.mkdtemp()) / path.name
        shutil.copy2(path, tmp)
        log.warning("Workbook locked; reading a temporary copy %s", tmp)
        return load_workbook(tmp, read_only=True, data_only=True), tmp


def list_sheets(path: Path = config.SOURCE_WORKBOOK) -> list[str]:
    wb, tmp = _open(path)
    try:
        return list(wb.sheetnames)
    finally:
        wb.close()
        if tmp:
            shutil.rmtree(tmp.parent, ignore_errors=True)


def extract(topics: list[str], path: Path = config.SOURCE_WORKBOOK) -> tuple[list[RawSheet], list[SheetDecision]]:
    """Read every selected tab of the requested topics. Returns (sheets, all selection decisions)."""
    if not path.exists():
        raise FileNotFoundError(f"Workbook not found: {path}")
    wb, tmp = _open(path)
    sheets, decisions = [], []
    try:
        for topic in topics:
            spec = config.TOPICS[topic]
            for d in select_sheets(list(wb.sheetnames), spec):
                decisions.append(d)
                if not d.selected:
                    log.info("%s: skip %s (%s)", topic, d.name, d.reason)
                    continue
                rows = [tuple(r) for r in wb[d.name].iter_rows(values_only=True)]
                sheets.append(RawSheet(d.name, parse_release(d.name), topic, rows))
                log.debug("%s: read %s (%d rows)", topic, d.name, len(rows))
    finally:
        wb.close()
        if tmp:
            shutil.rmtree(tmp.parent, ignore_errors=True)
    log.info("Extracted %d tab(s) for %s", len(sheets), ", ".join(topics))
    return sheets, decisions
