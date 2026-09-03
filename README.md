Quantitative Agricultural Research & Analytics Workspace
Welcome to the central repository for quantitative modeling, market intelligence pipelines, and trading research across global agricultural commodities, derivatives, and macroeconomic trends.

This repository consolidates analytical frameworks, automated data ingestion pipelines, and interactive web dashboards designed to evaluate supply-demand dynamics, options volatility, and physical commodity flow bottlenecks.

Directory Overview
CGC_Analyzer/

Description: Continuous tracking and analytics platform for Canadian Grain Commission (CGC) bulk grain logistics and vessel movement.

Key Components:

app.py: Streamlit dashboard entry point optimized for iFrame deployment.

ingestion.py & cgc_engine.py: Dynamic ETL and analytical calculation engine for grain handling statistics, capacity utilization, and seasonal pacing.

CGC_Capacity.xlsb: Physical terminal capacity benchmark dataset.

CFTC_Analyzer/

Description: Quantitative tracking of CFTC Commitments of Traders (COT) reports.

Key Components: Evaluates institutional positioning, net speculative length, and money manager flow shifts across agricultural futures and options contracts.

Corn Volatility/

Description: Black-76 option pricing and volatility surface engine for CBOT Corn futures.

Key Components: Historical volatility modeling, implied volatility smile/skew calculation, and automated harvesting scripts for options surface visualization.

Canola Cost of Carry/

Description: Storage, interest, and Variable Storage Rate (VSR) modeling for ICE Canola futures.

Key Components: Cost-of-carry matrix evaluation and calendar spread arbitrage models across crop-year transition periods.

CIMTD_Analyzer/

Description: International merchandise trade data analytics.

Key Components: Historical trade balance pivoting and macro-level export/import flow analysis.
