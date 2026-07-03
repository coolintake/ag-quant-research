"""
main.py
=======
Application entry point: coordinates data harvesting, surface fitting,
arbitrage validation, and presentation for the Corn Volatility Engine.

Run with:
    python main.py
"""

import asyncio
import logging

from config import CONFIG
from domain.calendar import is_wasde_lock
from data.harvester import CornDataHarvester
from models.surface import VolatilityEngine
from visualization.presenter import SurfacePresenter

logging.basicConfig(
    level=CONFIG['log_level'],
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)


async def main():
    if is_wasde_lock():
        logger.warning("MARKET LOCK: USDA WASDE REPORT IN PROGRESS. Pausing harvester for data integrity.")
        return

    harvester = CornDataHarvester()
    engine = VolatilityEngine()
    presenter = SurfacePresenter()

    try:
        if CONFIG['offline_mode']:
            logger.info("MODE: OFFLINE. Analyzing historical snapshot from file.")
            raw_df = harvester.load_offline_data()
            if raw_df.empty:
                logger.warning("No offline data available. Exiting.")
                return
        else:
            await harvester.connect()
            fut_prices = await harvester.get_futures_prices(CONFIG['target_futures_months'])
            if not fut_prices:
                return

            raw_df = await harvester.get_option_market_data(fut_prices)
            if raw_df.empty:
                logger.warning("No option data collected.")
                return

        # Math
        clean_df = engine.process_data(raw_df)

        # Arbitrage checks
        butterflies = engine.check_butterfly_arbitrage(clean_df)
        calendars = engine.check_calendar_arbitrage(clean_df)
        for v in butterflies + calendars:
            logger.warning(f"Arbitrage Violation: {v}")

        spline = engine.fit_surface(clean_df)
        metrics = engine.calculate_fit_metrics(clean_df, spline)

        # Present
        if not clean_df.empty:
            presenter.report_surface_quality(metrics)
            presenter.generate_plots(clean_df, spline, metrics)
            presenter.plot_term_structure(clean_df, spline)
            presenter.analyze_smile_skew(clean_df, spline)
            presenter.plot_smile_dashboard(clean_df, spline)
            presenter.plot_residuals_trading_map(clean_df, spline)
            presenter.export_data(clean_df)
            presenter.report_opportunities(clean_df, spline)
        else:
            logger.warning("No valid clean results to present.")

    except Exception as e:
        logger.error(f"Main Loop Error: {e}", exc_info=True)
    finally:
        harvester.disconnect()


if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
