# Quantitative Agricultural Research & Analytics Workspace

Welcome to the central repository for quantitative modeling, market intelligence pipelines, and trading research across global agricultural commodities, derivatives, and macroeconomic trends. 

This repository consolidates analytical frameworks, automated data ingestion pipelines, and interactive web dashboards designed to evaluate supply-demand dynamics, options volatility, and physical commodity flow bottlenecks.

---

## Directory Overview

### Market Intelligence & Logistics

* **CGC_Analyzer/** – Turns Canadian Grain Commission weekly statistics into a Streamlit dashboard, a weekly grain handling report and a monthly export workbook.
* **CFTC_Analyzer/** – Analyzes CFTC disaggregated Commitments of Traders reports from 2010 onward to track positioning in agricultural futures.
* **CIMTD_Analyzer/** – Builds commodity export and import workbooks from Canadian International Merchandise Trade data stored in `CIMTD/`.

### Canadian Supply, Demand & Pricing

* **Workspace_CanadaSD/** – Builds Canadian supply and demand balance sheets for grains, oilseeds, pulses, vegetable oils and protein meals from StatCan and CIMT data.
* **Canada_Crop_Prices/** – Stores Western Canadian cash grain prices in a local DuckDB database and calculates provincial averages for pre-planting price windows.
* **Matrix_FOB_Prices/** – Excel-based FOB pricing calculator, refreshed by batch launchers and Python scripts, that adds Hammersmith world market data.

### Crop Economics & Acreage

* **RPA_Calculator/** – Calculates Prairie crop returns over direct cash inputs from Alberta, Saskatchewan and Manitoba budget guides.
* **Canada_Planting_Model/** – Compares relative crop profitability with StatCan seeded acres to evaluate acreage signals and forecasts.

### Derivatives & Spreads

* **Corn Volatility/** – Builds a Black-76 implied volatility surface for CBOT corn options and checks it for arbitrage.
* **Canola Cost of Carry/** – Models cost of carry and Variable Storage Rate behavior for ICE canola futures using live interest rates and broker data.
* **Chart_Spreads/** – Produces seasonal charts of grain and oilseed futures calendar spreads from Barchart history.

### Shared Resources

* **Design/** – Holds the house style and language guides used for tables, charts and reports.

---

## Technical Setup & Standards

* **Language & Core Libraries:** Python 3.10+ (Pandas, NumPy, Streamlit, Plotly, PyArrow/Parquet)
* **Path Standardization:** File system accesses utilize dynamic resolution (Path(__file__).resolve().parent) to maintain platform independence between local environments and cloud runtimes.
* **Streamlit Configuration:** Repository-root .streamlit/config.toml overrides CORS and XSRF restrictions to support seamless iFrame integration on external CMS platforms (*Chilmeran Research*).
