
# Ag Quant Research

Selected Python/Jupyter notebooks from my research & trading work in Grain & Oilseed markets.

## Notebooks

**CFTC_Analyzer.ipynb**
Automated pipeline for CFTC Commitment of Traders (COT) data across eight commodities, tracking managed money vs. commercial positioning over time.

**CIMTD Pivot_v1.0.ipynb**
Automated pipeline for Statistics Canada's international trade data (CIMT). Downloads and parses HS6-level import/export records by commodity, building structured YoY pivot tables (canola, wheat, durum, corn, barley, soybeans) for trade-flow analysis.

**Canola_StorageCalc.ipynb**
Cost-of-carry model for canola futures using IBKR futures data and CORRA short-term rates, comparing spread pricing against full financial carry to identify basis and roll opportunities.

**Corn_Vol_Model.ipynb**
Quant options valuation & IV framework for CBOT Corn futures. Implements a Black-76 baseline alongside a Heston stochastic vol model calibrated to market data, utilizing bivariate smoothing algorithms to generate localized implied volatility surfaces and option skew profiles for structural options trading.
