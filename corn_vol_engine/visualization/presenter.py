"""
visualization/presenter.py
===========================
The Output Layer: Plotly visualizations -- 3D vol surface, smile dashboard,
term structure, skew analysis, and the residual (Market IV - Model IV)
trading map. Also handles data export and opportunity reporting.
"""

import logging
import math
from datetime import datetime

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots

from config import CONFIG
from domain.calendar import get_seasonal_context

logger = logging.getLogger(__name__)


class SurfacePresenter:
    """Visualization and reporting layer."""

    @staticmethod
    def _get_futures_shorthand(future_symbol):
        """Converts IB future symbol (e.g., ZCZ6) to shorthand like CZZ26."""
        if isinstance(future_symbol, str) and len(future_symbol) >= 4:
            month_code = future_symbol[2]
            year_digit = future_symbol[3]
            return f"CZ{month_code}2{year_digit}"
        return str(future_symbol)

    @staticmethod
    def generate_plots(df, spline, metrics=None):
        if not spline:
            logger.warning("No spline available for plotting.")
            return

        mi = np.linspace(df['moneyness_kf'].min(), df['moneyness_kf'].max(), 50)
        di = np.linspace(df['days_to_expiry'].min(), df['days_to_expiry'].max(), 50)
        M, D = np.meshgrid(mi, di)
        Z = spline.ev(M, D)

        title = "Corn (ZC) Volatility Surface 2026"
        seasonal_phase = get_seasonal_context()
        title += f" | Season: {seasonal_phase}"

        if metrics and metrics.get('r2', 1.0) < 0.90:
            title += " - <span style='color:red'>LOW CONFIDENCE MODEL</span>"

        fig = go.Figure(data=[
            go.Surface(x=M, y=D, z=Z, colorscale='Viridis', opacity=0.8),
            go.Scatter3d(x=df['moneyness_kf'], y=df['days_to_expiry'], z=df['iv'],
                         mode='markers', marker=dict(size=2, color='red'))
        ])
        fig.update_layout(title=title, scene=dict(
            xaxis_title='Moneyness (K/F)', yaxis_title='DTE', zaxis_title='IV'
        ))
        fig.show()

    @staticmethod
    def report_surface_quality(metrics):
        r2, mae = metrics.get('r2', 0), metrics.get('mae', 0)
        logger.info(f"Surface Fit Quality: R-squared = {r2:.4f}, MAE = {mae:.4f}")
        if r2 < 0.90:
            logger.warning("Fit Quality Low (R2 < 0.90): Model may use noisy data.")

    @staticmethod
    def plot_residuals_trading_map(df, spline):
        if not spline or df.empty:
            logger.warning("Cannot plot trading map: Spline or Data missing.")
            return

        df = df.copy()
        df['model_iv'] = spline.ev(df['moneyness_kf'], df['days_to_expiry'])
        df['residual'] = df['iv'] - df['model_iv']

        def get_signal(res):
            if res > 0.025:
                return 'SELL (Overpriced)'
            if res < -0.025:
                return 'BUY (Underpriced)'
            return 'Fair Value'

        df['signal'] = df['residual'].apply(get_signal)
        color_map = {'SELL (Overpriced)': 'red', 'BUY (Underpriced)': 'green', 'Fair Value': 'gray'}

        fig = px.scatter(df, x='moneyness_kf', y='residual', color='signal',
                         color_discrete_map=color_map,
                         hover_data=['strike', 'expiry', 'iv', 'model_iv'],
                         title="Corn (ZC) 2026 Residual Trading Map (Market IV - Model IV)")

        fig.add_hrect(y0=0.025, y1=max(0.05, df['residual'].max()), fillcolor="red", opacity=0.1, line_width=0, annotation_text="Overpriced Zone")
        fig.add_hrect(y0=min(-0.05, df['residual'].min()), y1=-0.025, fillcolor="green", opacity=0.1, line_width=0, annotation_text="Underpriced Zone")

        fig.add_hline(y=0, line_dash="dash", line_color="black", opacity=0.5)
        fig.update_layout(xaxis_title="Moneyness (K/F)", yaxis_title="Residual (IV Edge)")
        fig.show()

    @staticmethod
    def plot_smile_dashboard(df, spline):
        """Creates a grid of volatility smiles for each contract month."""
        if not spline or df.empty:
            logger.warning("Cannot plot smile dashboard: Spline or Data missing.")
            return

        expiries = sorted(df['expiry'].unique())
        # Grid sized dynamically to the actual number of expiries present.
        cols = 2
        rows = max(1, math.ceil(len(expiries) / cols))

        titles = []
        for exp in expiries:
            subset = df[df['expiry'] == exp]
            if not subset.empty:
                fut_sym = subset.iloc[0]['future_symbol']
                titles.append(SurfacePresenter._get_futures_shorthand(fut_sym))
            else:
                titles.append(exp)

        fig = make_subplots(rows=rows, cols=cols, subplot_titles=titles, vertical_spacing=0.1)

        for i, exp in enumerate(expiries):
            row = (i // cols) + 1
            col = (i % cols) + 1

            group = df[df['expiry'] == exp].sort_values('moneyness_kf')
            dte = group['days_to_expiry'].iloc[0]

            mi = np.linspace(group['moneyness_kf'].min() * 0.95, group['moneyness_kf'].max() * 1.05, 50)
            theo_iv = spline.ev(mi, np.full_like(mi, dte))

            fig.add_trace(go.Scatter(x=mi, y=theo_iv, mode='lines', name=f'Fair Value {exp[:6]}', line=dict(color='black', width=1), showlegend=False), row=row, col=col)

            group = group.copy()
            group['model_iv'] = spline.ev(group['moneyness_kf'], group['days_to_expiry'])
            group['diff'] = group['iv'] - group['model_iv']

            def get_color(diff):
                if diff > 0.02:
                    return 'red'
                if diff < -0.02:
                    return 'green'
                return 'gray'

            group['color'] = group['diff'].apply(get_color)

            fig.add_trace(go.Scatter(
                x=group['moneyness_kf'], y=group['iv'],
                mode='markers',
                marker=dict(color=group['color'], size=6, line=dict(width=1, color='DarkSlateGrey')),
                text=[f"Strike: {s}<br>Edge: {d:.1%}" for s, d in zip(group['strike'], group['diff'])],
                name=f'Market {exp[:6]}',
                showlegend=False
            ), row=row, col=col)

            fig.update_xaxes(title_text="Moneyness (K/F)", row=row, col=col)
            fig.update_yaxes(title_text="IV", row=row, col=col)

        fig.update_layout(height=1000, title_text="Corn (ZC) Volatility Smile Dashboard (Model vs Market)", showlegend=False)
        fig.show()

    @staticmethod
    def plot_term_structure(clean_df, spline):
        if not spline or clean_df.empty:
            return

        expiries = sorted(clean_df['expiry'].unique())
        term_data = []

        for exp in expiries:
            dte = clean_df[clean_df['expiry'] == exp]['days_to_expiry'].iloc[0]
            atm_iv = float(spline.ev(1.0, dte))
            term_data.append({'Expiry': exp, 'DTE': dte, 'ATM_IV': atm_iv})

        df_term = pd.DataFrame(term_data).sort_values('DTE')
        fig = px.line(df_term, x='Expiry', y='ATM_IV', markers=True,
                      title="Corn (ZC) Term Structure (ATM IV)",
                      labels={'ATM_IV': 'ATM Implied Volatility'})
        fig.add_hline(y=df_term['ATM_IV'].mean(), line_dash="dot", annotation_text="Avg IV")
        fig.show()

    @staticmethod
    def analyze_smile_skew(clean_df, spline):
        if not spline or clean_df.empty:
            return

        expiries = sorted(clean_df['expiry'].unique())
        skew_results = []

        for exp in expiries:
            group = clean_df[clean_df['expiry'] == exp]
            dte = group['days_to_expiry'].iloc[0]
            fut_sym = group['future_symbol'].iloc[0]
            shorthand = SurfacePresenter._get_futures_shorthand(fut_sym)

            # K/F < 1.0 means lower strike (OTM Put)
            # K/F > 1.0 means higher strike (OTM Call)
            put_iv_10pct = float(spline.ev(0.90, dte))   # 10% OTM put
            call_iv_10pct = float(spline.ev(1.10, dte))  # 10% OTM call
            atm_iv = float(spline.ev(1.0, dte))          # ATM Anchor

            put_skew = put_iv_10pct - atm_iv
            call_skew = call_iv_10pct - atm_iv

            skew_results.append({
                'Label': shorthand,
                'Expiry': exp,
                'Put Skew (10% OTM)': put_skew * 10000,
                'Call Skew (10% OTM)': call_skew * 10000,
                'ATM IV': atm_iv * 100,
                'Asymmetry': (put_skew - call_skew) * 10000
            })

            if put_skew > call_skew + 0.02:
                sentiment = "Bearish Skew (Downside protection heavily bid)"
            elif call_skew > put_skew + 0.02:
                sentiment = "Bullish Skew (Upside supply concerns/Call squeeze)"
            else:
                sentiment = "Normal/Balanced Commodity Skew"

            logger.info(
                f"Skew [{shorthand}]: Put Skew={put_skew:+.2%}, Call Skew={call_skew:+.2%} -> {sentiment}"
            )

        df_skew = pd.DataFrame(skew_results)

        fig = go.Figure()

        fig.add_trace(go.Bar(
            x=df_skew['Label'],
            y=df_skew['Put Skew (10% OTM)'],
            name='Put Skew (Downside Premium over ATM)',
            marker_color='red'
        ))

        fig.add_trace(go.Bar(
            x=df_skew['Label'],
            y=df_skew['Call Skew (10% OTM)'],
            name='Call Skew (Upside Premium over ATM)',
            marker_color='green'
        ))

        fig.add_hline(y=0, line_dash="dash", line_color="black",
                      annotation_text="ATM Reference (0 bps)")

        fig.update_layout(
            title="Volatility Skew: Premium Over ATM (Basis Points)",
            xaxis_title="Contract Month",
            yaxis_title="IV Skew (bps over/under ATM)",
            barmode='group'
        )

        fig.show()

    @staticmethod
    def export_data(df):
        try:
            data_path = CONFIG['data_path']
            if data_path.endswith('.xlsx') or data_path.endswith('.xls'):
                df.to_excel(data_path, index=False, engine='openpyxl')
            else:
                df.to_csv(data_path, index=False)
            logger.info(f"Data exported to {data_path}")
        except Exception as e:
            logger.error(f"Data export failed: {e}")

    @staticmethod
    def report_opportunities(df, spline):
        if not spline:
            return
        df = df.copy()
        df['model_iv'] = spline.ev(df['moneyness_kf'], df['days_to_expiry'])
        df['edge'] = df['iv'] - df['model_iv']

        opps = df[abs(df['edge']) > 0.02].sort_values('edge', ascending=False)
        if not opps.empty:
            print("\n" + "=" * 70)
            print("TRADING OPPORTUNITIES (Edge > 2%)")
            print("=" * 70)
            cols = ['future_symbol', 'strike', 'right', 'days_to_expiry', 'iv', 'model_iv', 'edge']
            print(opps[[c for c in cols if c in opps.columns]].to_string(index=False))
