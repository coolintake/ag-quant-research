# CIMT Trade Data Pipeline

Automated pipeline for Statistics Canada's international trade data (CIMT), 
building structured, commodity-level Excel workbooks from raw government 
trade files.

## What it does
- Reads CIMT zip files (imports and exports) covering 2012–present.
- Filters to HS6 codes across eight commodities: durum, wheat (ex-durum), 
  canola, soybeans, corn, barley, red lentils, and yellow peas.
- Resolves country codes to full names using a master lookup built across 
  all available files.
- Builds, per commodity, a multi-sheet Excel workbook containing:
  - Years x Months pivot tables (Exports and Imports)
  - Top destination countries by crop year
  - Regional breakdown by crop year (North America, LATAM, Europe, MENA, 
    Africa, Asia, Oceania)
  - A filterable raw data sheet (Country, HS6, Year, Month, volume, value)

## Data source
Statistics Canada CIMT (Canadian International Merchandise Trade), Open 
Government Portal.
<img width="1512" height="1127" alt="image" src="https://github.com/user-attachments/assets/2adb4b8b-7a78-48d5-bb6d-460cd917ca9a" />

## Requirements
`pandas`, `openpyxl`, `requests`

## Setup
Place downloaded CIMT zip files in a local `data/` folder before running.
