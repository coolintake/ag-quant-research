# CLAUDE.md - System Context & Operational Instructions

## 1. System Identity & Scope Boundary
- Repository Scope: Exclusively handles local data ingestion, normalization, DuckDB/Parquet storage, and 30-day window price aggregation for Western Canadian grain prices.
- Strictly Forbidden: Do NOT implement log-log regressions, acreage elasticity calculations, or planting allocation models in this repository. Those belong in `Canada_Planting_Model`.
- Code Modification Guardrail: NEVER overwrite, rename, or restructure existing core logic in `config.py`, `database.py`, `pdq_parsing.py`, `ingest.py`, or `price_aggregator.py` unless explicitly directed.
- Local Data Only: The PDQ API subscription has ended (2026-10). Never add network calls to PDQ. Prices arrive only as local files: saved JSON snapshots in `data/raw/`, PDQ website CSV exports in `input/` (`backfill_pdq.py`), and manual CSVs (`ingest.py --import-csv`).

## 2. Core Methodology & Data Pipeline Constraints
- Target Pricing Windows (RPA 1.0 / 2.0):
  - Run 1: Dec 15 – Jan 15
  - Run 2: Jan 15 – Feb 15
  - Run 3: Feb 15 – Mar 15
- Target Futures Contracts / Delivery Months:
  - Cereals/Corn: December (`Z`) contracts/delivery months.
  - Oilseeds (Canola/Soybeans): November (`X`) contracts/delivery months.
- Commodity Ticker Standardization:
  - `1 CWRS 13.5` -> `CWRS`
  - `1 CWAD 13.0` -> `DURUM`
  - `1 CDA CANOLA` -> `CANOLA`
  - `2 YELLOW PEAS` -> `Y_PEAS`
  - `1 CPSR 11.5` -> `CPSR`
- Data Integrity Rules:
  - Missing or unquoted prices (`0.0`) MUST be stored as `NULL` in DuckDB/Parquet. Never allow phantom zeros into downstream averages.
  - Derive `delivery_year` dynamically to disambiguate rolling delivery months.
  - Database paths must use relative `pathlib.Path` from `config.py`. Never hardcode absolute file paths.

## 3. Execution Commands
- Load PDQ Website Exports: `python backfill_pdq.py`
- Import Manual CSV: `python ingest.py --import-csv FILE.csv`
- Reload DB from Raw: `python ingest.py --reload-raw`
- Export Parquet: `python ingest.py --export-parquet`
- Calculate Averages: `python price_aggregator.py --crop-year YYYY`
- Run Tests: `pytest tests/`
