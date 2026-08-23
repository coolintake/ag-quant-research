import plotly.graph_objects as go

class VolVisualizer:
    @staticmethod
    def plot_3d(S_mesh, T_mesh, IV_mesh):
        fig = go.Figure(data=[go.Surface(x=T_mesh, y=S_mesh, z=IV_mesh, colorscale='Viridis')])
        fig.update_layout(
            title='Corn Options Volatility Surface',
            scene=dict(
                xaxis_title='Time to Expiry (Years)',
                yaxis_title='Strike Price',
                zaxis_title='Implied Volatility'
            ),
            width=900, height=700
        )
        return fig