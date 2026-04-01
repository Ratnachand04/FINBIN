from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go


def price_chart(df: pd.DataFrame, symbol: str) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(
        go.Candlestick(
            x=df["timestamp"],
            open=df["open"],
            high=df["high"],
            low=df["low"],
            close=df["close"],
            name=symbol,
        )
    )
    fig.update_layout(title=f"{symbol} Price", xaxis_title="Time", yaxis_title="Price")
    return fig
