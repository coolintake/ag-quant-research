"""Download the Hammersmith parser test fixtures (one-time; re-run only to add fixtures).

    python Hammersmith/build_fixtures.py            # fetch any missing fixture
    python Hammersmith/build_fixtures.py --force    # re-fetch all

Fixtures are chosen to cover every format era and every known post oddity. They are identified by their
published timestamp (feed positions shift as new posts arrive) and saved as gzipped JSON posts in
tests/fixtures/hammersmith/<name>.json.gz.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import hammersmith_scraper as hs

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "hammersmith"

# name -> (published timestamp prefix, why it is here)
FIXTURES: dict[str, tuple[str, str]] = {
    "2026-10-17": ("2026-10-17T13:32:15", "current format; Nov<<Jan window; '<<' and '>>' quotes"),
    "2026-10-03": ("2026-10-03T15:43:39", "'USD no prices'; Oct<<Dec window"),
    "2026-05-30_fw": ("2026-06-01T10:26:59", "'FW:' title, published two days after the report date"),
    "2026-05-16_correction": ("2026-05-16T22:34:21", "'Correction ---' title"),
    "2025-11-15": ("2025-11-15T15:24:00", "'Hammersmith Report --- 11 15 2025' numeric title date"),
    "2025-09-05_not_report": ("2025-09-05T09:55:00", "forwarded note, not a report"),
    "2025-05-24": ("2025-05-24T18:20:00", "2025 format"),
    "2025-01-18_no_report": ("2025-01-18T09:01:00", "'Sorry no Weekly Report this week'"),
    "2024-11-30": ("2024-11-30T15:30:00", "2024 format"),
    "2024-03-17_blank_title": ("2024-03-17T12:18:00", "empty title"),
    "2023-10-28": ("2023-10-28T11:52:00", "'Nov 2023/Jan 2024' header; '<<' with far-month suffix 'Feb'"),
    "2022-09-17": ("2022-09-17T10:45:00", "'430 -- 420 December' dash form; freight '$61/62.00' ranges"),
    "2022-07-02_resend": ("2022-07-03T23:30:00", "'Fw: Resending' duplicate of a report date"),
    "2021-07-10": ("2021-07-10T14:10:00", "'new crop' quotes; 'X' column; tonnage-only vessel specs"),
    "2020-09-26_corrected": ("2020-09-26T12:38:00", "'Weekly Rpt -- ... -- corrected date' title"),
    "2020-08-22": ("2020-08-22T12:09:00", "bid/offer era with a '>>' forward quote"),
    "2019-11-23": ("2019-11-23T12:37:00", "'December 2019/January 2020' header across a year end"),
    "2019-06-08": ("2019-06-08T16:15:00", "'May/July 2019' one-year header; row-level 'July/Sep New'"),
    "2019-01-05": ("2019-01-05T12:06:00", "first report in scope"),
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="re-fetch fixtures that already exist")
    args = ap.parse_args(argv)

    todo = {n: v for n, v in FIXTURES.items() if args.force or not (FIXTURE_DIR / f"{n}.json.gz").exists()}
    if not todo:
        print("All fixtures present.")
        return 0
    index = hs.fetch_index()
    missing = 0
    for name, (stamp, why) in todo.items():
        pos = next((x["position"] for x in index if x["published"].startswith(stamp)), None)
        post = hs.fetch_entry(pos) if pos else None
        if post is None or not post["published"].startswith(stamp):
            print(f"MISSING  {name}: no post published at {stamp}")
            missing += 1
            continue
        hs.save_post(post, FIXTURE_DIR / f"{name}.json.gz")
        print(f"saved    {name}  ({len(post['content']) / 1e6:.2f} MB)  {why}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
