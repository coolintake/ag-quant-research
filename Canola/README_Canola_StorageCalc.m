# Canola Cost-of-Carry Calculator

Live cost-of-carry model for canola futures, comparing calculated full 
financial carry against actual market spread pricing across the futures 
curve.

## What it does
- Pulls the current Canadian Overnight Repo Rate Average (CORRA) live from 
  the Bank of Canada, adds a fixed broker spread, to derive the applied 
  annual interest rate.
- Connects to Interactive Brokers (IBKR TWS API) to retrieve live/last 
  closing prices for canola futures contracts across the curve.
- Calculates cost-of-carry for every combination of contract months, 
  incorporating a daily storage rate.
- Displays results as a color-coded heatmap, showing which spread 
  combinations are trading rich or cheap relative to full carry.

## Requirements
`ibapi`, `pandas`, `numpy`, `matplotlib`, `seaborn`, `requests`

## Note
Requires a running IBKR TWS or Gateway connection (socket port configurable) 
to fetch live futures prices.
