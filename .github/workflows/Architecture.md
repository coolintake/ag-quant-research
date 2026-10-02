# Daily PDQ Fetch: How It Works

This document explains how PDQ cash prices are collected every weekday by
GitHub Actions, stored in the repository, and pulled into your local DuckDB and
Parquet storage. The workflow file is `daily_pdq_fetch.yml`, in this folder.
The project code lives in `Canada_Crop_Prices/`.

## The short version

1. **GitHub fetches.** Every weekday evening, a GitHub server downloads the
   day's PDQ prices and saves them as one JSON file.
2. **GitHub commits.** That JSON file is committed to
   `Canada_Crop_Prices/data/raw/` on `main`. Nothing else is committed.
3. **You sync.** When you want fresh data on your machine, you run `git pull`
   and then `python ingest.py --reload-raw`. That rebuilds your local DuckDB
   database and Parquet files from the JSON files in a few seconds.

Your computer doesn't need to be on at fetch time. The raw JSON files in git
are the permanent record; the database and Parquet files are copies you can
rebuild from them at any time.

```
 GitHub (every weekday, 00:00 UTC)               Your machine (whenever you like)
 ─────────────────────────────────               ────────────────────────────────
 PDQ API ──► ingest.py --fetch-pdq               git pull
                │                                   │
                ▼                                   ▼
     data/raw/YYYY-MM-DD_pdq.json  ──commit──►  data/raw/*.json
                                    to main         │
                                                    ▼
                                         ingest.py --reload-raw
                                                    │
                                     ┌──────────────┴──────────────┐
                                     ▼                             ▼
                          data/crop_prices.duckdb      data/parquet/year=/month=/
```

## 1. The remote fetch on GitHub Actions

**When it runs.** The job runs at 00:00 UTC Tuesday through Saturday. That is
8:00 PM EDT (7:00 PM EST in winter) on Monday through Friday evening, after PDQ
has posted the day's prices. The schedule is shifted one day in UTC on
purpose: a UTC Monday-to-Friday schedule would run on Sunday evening in Canada
and miss Friday's prices. GitHub can start scheduled jobs several minutes late
when its servers are busy; that doesn't matter here.

You can also start it by hand from the repository's **Actions** tab ("Daily PDQ
Price Fetch" → "Run workflow"). Use this to test it or to catch up after an
outage.

**What it does, step by step.**

1. Checks out the repository on a fresh Ubuntu machine.
2. Installs Python 3.11 and the packages in `Canada_Crop_Prices/requirements.txt`.
3. Runs `python ingest.py --fetch-pdq` with the API key from the repository
   secret `PDQ_API_KEY`. If that secret is missing, the job stops with a clear
   error instead of calling PDQ with a broken address.
4. Commits any new file in `data/raw/` and pushes it to `main`.

**The API key.** The key is stored as a GitHub repository secret
(**Settings → Secrets and variables → Actions → New repository secret**, name
`PDQ_API_KEY`). GitHub passes it to the job as an environment variable,
`config.py` reads it from there, and GitHub hides it in the job logs.

**When nothing changes.** PDQ doesn't post new prices on holidays. If the
fetch returns an import already saved, no new file is written. The job then
sees nothing to commit and finishes successfully without a commit.

**When something fails.** If PDQ is down or returns bad data, `ingest.py`
exits with an error, the job is marked failed, and GitHub emails you (by
default). Nothing is committed, so no bad data gets into the repository. Run
the job again by hand once PDQ is back. A missed day is simply a gap:
`ingest.py` can only fetch PDQ's current prices, not past days.

## 2. How the raw JSON files are stored

Each fetch writes PDQ's response exactly as received to:

```
Canada_Crop_Prices/data/raw/YYYY-MM-DD_pdq.json
```

The date comes from PDQ's own `prices_imported_at` timestamp, not from the
clock on GitHub's server. If PDQ publishes a second, different import on the
same day, it is saved alongside the first as `YYYY-MM-DD_HHMM_pdq.json`.
Existing files are never overwritten.

Only `data/raw/` goes into git. The DuckDB database and the Parquet folder are
listed in `Canada_Crop_Prices/.gitignore`. The GitHub server builds them during
the fetch, but they are discarded when the job ends.

**Size.** Each snapshot is about 70 KB, or roughly 18 MB a year of weekday
files. That's small enough to keep in git without trouble.

The commit message includes `[skip ci]`, so the daily commit doesn't trigger
other workflows. Before pushing, the job pulls in any commits you made since it
started, so it won't overwrite your work.

## 3. Synchronizing your local machine

Run these from `Canada_Crop_Prices/` whenever you want up-to-date prices
locally, for example before a pricing-window run:

```bash
git pull
python ingest.py --reload-raw
```

`git pull` downloads the new JSON files. `--reload-raw` then reads every JSON
file in `data/raw/` and loads it into `data/crop_prices.duckdb`:

- **It can be repeated safely.** Loading an import that's already in the
  database replaces it rather than adding a copy. Running `--reload-raw` twice
  in a row gives the same result.
- **It keeps your manual prices.** Prices you added with `--import-csv`
  (Barchart, CME, manual) are left untouched; only PDQ rows are rebuilt.
- **It refreshes Parquet as it goes.** After loading, it rewrites the Parquet
  files for the months it touched, under `data/parquet/year=YYYY/month=MM/`.
  To force a complete Parquet rebuild, add `--export-parquet`.

**Why this won't strain your machine.** The heavy, always-on part (waking up
every weekday and calling PDQ) runs on GitHub's servers. Your machine only
does work when you choose to sync. Syncing reads small JSON files into an
embedded database, with no server or background process involved. A
single-day reload takes about 3 seconds today. A full year of snapshots is
about 70,000 rows, which DuckDB loads in seconds while using little memory.

Then compute window averages as usual:

```bash
python price_aggregator.py --crop-year 2027
```

## 4. Rules that keep this conflict-free

- **Don't also run `--fetch-pdq` locally on a schedule.** If your machine saves
  a snapshot file that GitHub has also committed, `git pull` will refuse to
  overwrite your untracked copy. If this happens, delete the local file named
  in the error and pull again; the committed copy has the same content.
- **Don't edit or delete files in `data/raw/` by hand.** They are the source
  of truth. To correct a price, add a manual override CSV with
  `--import-csv` instead.
- **The DuckDB file is local-only.** Never commit it. It is rebuilt from
  `data/raw/` plus your manual CSVs.

## 5. One-time setup checklist

1. Commit and push `Canada_Crop_Prices/` (code, `requirements.txt`,
   `.gitignore`) and this workflow to `main`. GitHub only runs scheduled
   workflows from the repository's default branch.
2. Add the repository secret `PDQ_API_KEY`.
3. Check that Actions has write access: **Settings → Actions → General →
   Workflow permissions**. The workflow asks for `contents: write`, but an
   organization policy can override that.
4. Run the workflow once by hand from the Actions tab and confirm a commit
   named "Auto-update PDQ raw price snapshot" appears on `main`.

GitHub turns off scheduled workflows in public repositories after 60 days
with no repository activity. If the daily commits stop, check the Actions tab
for a "workflow disabled" notice and re-enable it.
