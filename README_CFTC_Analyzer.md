# CFTC Positioning Analyzer

Automated pipeline that downloads and processes CFTC Commitment of Traders 
(Disaggregated) reports from 2010–present across eight commodities: corn, 
SRW wheat, HRW wheat, HR Spring wheat, canola, soybeans, soybean oil, and 
WTI crude.

## What it does
- Downloads and concatenates historical COT data directly from the CFTC's 
  public archive.
- Standardizes exchange naming across contract migrations (e.g. HR Spring 
  wheat listings moving between exchanges over time).
- Aggregates net managed money positioning by commodity, year, month, and 
  week.
- Tabulates historical top long/short positioning extremes by commodity 
  and year.
- Visualizes current-year weekly positioning against the historical 
  min/max range for the same calendar week, by commodity — highlighting 
  when speculative positioning is stretched relative to its seasonal norm.

## Data source
CFTC Disaggregated Commitment of Traders reports (public, cftc.gov).

## Requirements
`pandas`, `matplotlib`, `tabulate`, `requests`
