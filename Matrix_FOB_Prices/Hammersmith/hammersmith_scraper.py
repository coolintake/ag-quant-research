"""Hammersmith weekly report -> world FOB + ocean freight drops for Matrix_FOB_Calculator.xlsx.

    python Hammersmith/hammersmith_scraper.py                           # weekly: new posts only
    python Hammersmith/hammersmith_scraper.py --backfill-from 2019-01-01  # every report since a date
    python Hammersmith/hammersmith_scraper.py --reparse                 # rebuild history from cached posts (parser change)
    python Hammersmith/hammersmith_scraper.py --offline                 # no network: rebuild drops from history

Flow
  Blogger JSON feed --> cache/hammersmith/raw/<published>.json.gz   (one file per post; re-downloadable)
                    --> hammersmith_parser.parse_post
                    --> data_drop/history/world_fob_history.parquet, world_freight_history.parquet
                        (upsert; one report per report_date - a resend/correction replaces the earlier post)
                    --> data_drop/world_fob_latest.csv, world_freight_latest.csv   (latest report, atomic write)

Review flags: a value that moved more than 15% from the previous report (same series and delivery month, or same
freight route) is flagged OUTLIER with a review_note. Flagged values stay in the data and in CIF.

run_pull.py calls run() and never fails because of this step; the result goes to the fx.csv run-metadata row.
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import os
import re
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

import hammersmith_parser as hp
import hs_config as hc

log = logging.getLogger("hammersmith")
RAW_DIR = hc.HAMMERSMITH_CACHE / "raw"
FAILED_DIR = hc.HAMMERSMITH_CACHE / "failed"
PRICE_DROP_COLS = hp.PRICE_COLUMNS + hc.PRICE_EXTRA
FREIGHT_DROP_COLS = hp.FREIGHT_COLUMNS + hc.FREIGHT_EXTRA


# --------------------------------------------------------------------------- #
# Feed access and raw cache
# --------------------------------------------------------------------------- #
def _get_json(url: str, retries: int = 3) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": hc.HAMMERSMITH_UA})
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.load(resp)
        except OSError as exc:
            if attempt == retries:
                raise
            log.warning("feed request failed (%s), retry %d/%d", exc, attempt, retries - 1)
            time.sleep(2 * attempt)
    raise AssertionError("unreachable")


def _entry(e: dict) -> dict:
    link = next((x["href"] for x in e.get("link", []) if x.get("rel") == "alternate"), "")
    return {"title": e["title"]["$t"], "published": e["published"]["$t"], "updated": e["updated"]["$t"],
            "link": link, "content": e.get("content", {}).get("$t", "")}


def fetch_entry(position: int) -> dict | None:
    """Full post at feed position `position` (1 = newest), or None past the end. The feed returns at most ~2
    full posts per call (each ~0.5-1.5 MB of Word HTML), so posts are fetched one at a time."""
    j = _get_json(f"{hc.HAMMERSMITH_FEED}?alt=json&max-results=1&start-index={position}")
    entries = j["feed"].get("entry", [])
    if not entries:
        return None
    post = _entry(entries[0])
    post["fetched_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    return post


def fetch_index(since: date | None = None, page: int = 25) -> list[dict]:
    """Position, title and timestamps of every post (newest first) published on/after `since`.
    Uses the summary feed (no post bodies) and stops paging once posts are older than `since`."""
    summary = hc.HAMMERSMITH_FEED.replace("/default", "/summary")
    out, start = [], 1
    while True:
        j = _get_json(f"{summary}?alt=json&max-results={page}&start-index={start}")
        entries = j["feed"].get("entry", [])
        if not entries:
            return out
        for i, e in enumerate(entries):
            pub = e["published"]["$t"]
            if since and pub[:10] < since.isoformat():
                return out
            out.append({"position": start + i, "title": e["title"]["$t"], "published": pub,
                        "updated": e["updated"]["$t"]})
        start += len(entries)


def save_post(post: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(post, f, ensure_ascii=False)


def load_post(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def raw_path(published: str) -> Path:
    return RAW_DIR / (re.sub(r"[^0-9T]", "", published[:19]) + ".json.gz")


def cached_posts(index: list[dict]) -> list[dict]:
    """Posts for `index`, from the raw cache unless missing or edited since (feed 'updated' changed)."""
    posts = []
    for n, item in enumerate(index, start=1):
        path = raw_path(item["published"])
        post = load_post(path) if path.exists() else None
        if post is None or post.get("updated") != item["updated"]:
            post = fetch_entry(item["position"])
            if post is None or post["published"] != item["published"]:
                raise RuntimeError(f"feed moved while fetching (expected post {item['published']}); run again")
            save_post(post, path)
            log.info("fetched %3d/%d  %s  %s", n, len(index), item["published"][:10], item["title"][:60])
        posts.append(post)
    return posts


# --------------------------------------------------------------------------- #
# History
# --------------------------------------------------------------------------- #
def _frames(results: list[tuple[dict, hp.ParseResult]]) -> tuple[pd.DataFrame, pd.DataFrame]:
    prices, freight = [], []
    for post, res in results:
        for r in res.prices:
            meta = hc.SERIES_META.get(r["series_id"], ("", ""))
            prices.append({**r, "display_name": meta[0], "freight_origin_key": meta[1],
                           "fetched_at": post.get("fetched_at", ""), "parse_flag": r["quality_flag"]})
        for r in res.freight:
            freight.append({**r, "origin_key": f"{r['origin']}|{r['origin_country']}",
                            "fetched_at": post.get("fetched_at", ""), "parse_flag": r["quality_flag"]})
    return pd.DataFrame(prices), pd.DataFrame(freight)


def _upsert(path: Path, new: pd.DataFrame) -> pd.DataFrame:
    """Merge into the Parquet history. One post per report_date: the latest-published post wins, so a
    resend or correction replaces the original report."""
    old = pd.read_parquet(path) if path.exists() else pd.DataFrame()
    df = pd.concat([old, new], ignore_index=True) if len(old) else new.copy()
    if df.empty:
        return df
    keep = df.groupby("report_date")["published_at"].transform("max")
    df = df[df.published_at == keep].drop_duplicates("key", keep="last")
    return df.sort_values(["report_date", "key"]).reset_index(drop=True)


def _weekly_moves(df: pd.DataFrame, value: str, group: list[str]) -> pd.DataFrame:
    """quality_flag = parse_flag, raised to OUTLIER where the value moved > HS_WEEKLY_MOVE vs the previous
    report for the same group. Recomputed over the whole history so it is idempotent."""
    df = df.sort_values(group + ["report_date"]).copy()
    prev_val = df.groupby(group)[value].shift(1)
    prev_date = df.groupby(group)["report_date"].shift(1)
    move = (df[value] - prev_val) / prev_val
    hit = move.abs() > hc.HS_WEEKLY_MOVE
    df["quality_flag"] = df["parse_flag"].where(~(hit & (df.parse_flag == "OK")), "OUTLIER")
    df["review_note"] = ""
    df.loc[hit, "review_note"] = ("w/w " + (move[hit] * 100).map("{:+.1f}%".format) + " vs "
                                  + prev_date[hit].astype(str) + " (" + prev_val[hit].map("{:.2f}".format) + ")")
    lo, hi = hc.HS_PRICE_RANGE if value == "fob_usd_mt" else hc.HS_FREIGHT_RANGE
    out_rng = df[value].notna() & ~df[value].between(lo, hi)
    df.loc[out_rng, "review_note"] = (df.loc[out_rng, "review_note"] + f"; outside {lo:g}-{hi:g} USD/MT"
                                      ).str.lstrip("; ")
    return df.sort_values(["report_date", "key"]).reset_index(drop=True)


def _cif_preference(fr: pd.DataFrame) -> pd.DataFrame:
    """cif_pref = 1 on the single freight row per report x origin key x destination that CIF uses:
    preferred vessel class (VESSEL_PREFERENCE), then the lower rate. Outliers stay eligible."""
    fr = fr.copy()
    fr["cif_pref"] = 0
    priced = fr[fr.freight_usd_mt.notna()].copy()
    if priced.empty:
        return fr
    rank = {c: i for i, c in enumerate(hc.VESSEL_PREFERENCE)}
    priced["_rank"] = priced.vessel_class.fillna("").map(lambda c: rank.get(c, len(rank)))
    best = priced.sort_values(["_rank", "freight_usd_mt"]).groupby(
        ["report_date", "origin_key", "destination"]).head(1).index
    fr.loc[best, "cif_pref"] = 1
    return fr


# --------------------------------------------------------------------------- #
# Drops
# --------------------------------------------------------------------------- #
def write_csv_atomic(df: pd.DataFrame, path: Path, retries: int = 3) -> None:
    """Write next to the target, then swap in one step: Power Query never sees a half-written file. Retries
    if the CSV is held open (e.g. opened in Excel, or OneDrive syncing it)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_csv(tmp, index=False, encoding="utf-8")
    for attempt in range(1, retries + 1):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == retries:
                tmp.unlink(missing_ok=True)
                raise
            log.warning("%s is locked (open in Excel?) - retry %d/%d", path.name, attempt, retries - 1)
            time.sleep(2)


def write_drops(prices: pd.DataFrame, freight: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    latest = prices.report_date.max()
    p = prices[prices.report_date == latest].reindex(columns=PRICE_DROP_COLS)
    f = freight[freight.report_date == latest].reindex(columns=FREIGHT_DROP_COLS)
    hc.DATA_DROP.mkdir(parents=True, exist_ok=True)
    write_csv_atomic(p, hc.WORLD_FOB_CSV)
    write_csv_atomic(f, hc.WORLD_FREIGHT_CSV)
    return p, f


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #
def _history_since() -> date | None:
    if not hc.WORLD_FOB_HISTORY.exists():
        return None
    last = pd.read_parquet(hc.WORLD_FOB_HISTORY, columns=["published_at"]).published_at.max()
    return (datetime.fromisoformat(last) - timedelta(days=21)).date()      # re-check resends/corrections


def run(as_of: date | None = None, backfill_from: date | None = None, offline: bool = False,
        reparse: bool = False) -> dict:
    """Fetch, parse, upsert history and write the drops. Returns the status row for fx.csv (never raises)."""
    as_of = as_of or date.today()
    status = {"world_report_date": "", "world_age_days": "", "world_status": "OK", "world_rows": 0,
              "world_freight_rows": 0, "world_outliers": 0, "world_unmapped": 0}
    try:
        results: list[tuple[dict, hp.ParseResult]] = []
        if reparse:                                     # rebuild history from scratch out of the raw cache
            paths = sorted(RAW_DIR.glob("*.json.gz"))
            results = [(post, hp.parse_post(post)) for post in map(load_post, paths)]
            for stale in [hc.WORLD_FOB_HISTORY, hc.WORLD_FREIGHT_HISTORY, *FAILED_DIR.glob("*.json.gz")]:
                stale.unlink(missing_ok=True)
        elif not offline:
            since = backfill_from or _history_since() or (as_of - timedelta(days=28))
            try:
                index = fetch_index(since)
                results = [(post, hp.parse_post(post)) for post in cached_posts(index)]
            except OSError as exc:
                log.error("Hammersmith feed unreachable: %s - keeping the previous drop", exc)
                status["world_status"] = "FETCH_FAILED"
        failed = [(p, r) for p, r in results if r.status == "PARSE_FAILED"]
        for post, res in failed:
            save_post(post, FAILED_DIR / raw_path(post["published"]).name)
            log.error("PARSE_FAILED %s (%s): %s", post["published"][:10], post["title"][:50], res.reason)
        reports = [(p, r) for p, r in results if r.status == "REPORT"]
        for p, r in reports:
            for issue in r.issues:
                log.warning("%s: %s", r.report_date, issue)
        if reports:
            prices, freight = _frames(reports)
            prices = _upsert(hc.WORLD_FOB_HISTORY, prices)
            freight = _upsert(hc.WORLD_FREIGHT_HISTORY, freight)
            prices = _weekly_moves(prices, "fob_usd_mt", ["series_id", "delivery_ym"])
            freight = _cif_preference(_weekly_moves(freight, "freight_usd_mt", ["route_id"]))
            hc.HISTORY_DIR.mkdir(parents=True, exist_ok=True)
            prices.to_parquet(hc.WORLD_FOB_HISTORY, index=False)
            freight.to_parquet(hc.WORLD_FREIGHT_HISTORY, index=False)
            log.info("history: %d price rows / %d freight rows over %d reports", len(prices), len(freight),
                     prices.report_date.nunique())
        elif hc.WORLD_FOB_HISTORY.exists():
            prices = pd.read_parquet(hc.WORLD_FOB_HISTORY)
            freight = pd.read_parquet(hc.WORLD_FREIGHT_HISTORY)
        else:
            if status["world_status"] == "OK":
                status["world_status"] = "NO_HISTORY"
            return status
        newest_post = max((p["published"] for p, _ in results), default="")
        newest_report = max((p["published"] for p, _ in reports), default="")
        if failed and newest_post > newest_report:
            status["world_status"] = "PARSE_FAILED"
        try:
            p, f = write_drops(prices, freight)
        except PermissionError:
            status["world_status"] = "WRITE_LOCKED"
            latest = prices.report_date.max()
            p, f = prices[prices.report_date == latest], freight[freight.report_date == latest]
        rd = date.fromisoformat(p.report_date.iloc[0])
        status.update(world_report_date=rd.isoformat(), world_age_days=(as_of - rd).days, world_rows=len(p),
                      world_freight_rows=len(f),
                      world_outliers=int((p.quality_flag == "OUTLIER").sum() + (f.quality_flag == "OUTLIER").sum()),
                      world_unmapped=int((p.quality_flag == "UNMAPPED").sum() + (f.quality_flag == "UNMAPPED").sum()))
        log.info("world drops: report %s (%d days old), %d price rows, %d freight rows, %d outliers, %d unmapped - %s",
                 rd, status["world_age_days"], len(p), len(f), status["world_outliers"], status["world_unmapped"],
                 status["world_status"])
    except Exception as exc:                                   # never break the PDQ refresh
        log.exception("Hammersmith step failed: %s", exc)
        status["world_status"] = f"ERROR: {type(exc).__name__}"
    return status


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    ap.add_argument("--backfill-from", type=date.fromisoformat, help="fetch every report published since this date")
    ap.add_argument("--reparse", action="store_true", help="re-parse every cached post (no network)")
    ap.add_argument("--offline", action="store_true", help="no network: rebuild the drops from history")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    status = run(args.as_of, args.backfill_from, args.offline, args.reparse)
    return 0 if status["world_status"] == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())
