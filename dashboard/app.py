from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import streamlit as st

from dashboard.components.charts import price_chart

st.set_page_config(page_title="BINFIN Dashboard", layout="wide")
st.title("BINFIN Crypto Intelligence")

symbol = st.selectbox("Symbol", ["BTCUSDT", "ETHUSDT", "SOLUSDT"])

now = datetime.now(UTC)
rows = []
price = 70000.0 if symbol == "BTCUSDT" else 3500.0
for idx in range(96):
    ts = now - timedelta(minutes=(96 - idx) * 15)
    rows.append(
        {
            "timestamp": ts,
            "open": price,
            "high": price * 1.003,
            "low": price * 0.997,
            "close": price * (1 + ((idx % 10) - 5) / 1000),
        }
    )
    price = rows[-1]["close"]

df = pd.DataFrame(rows)
st.plotly_chart(price_chart(df, symbol), use_container_width=True)

col1, col2, col3 = st.columns(3)
col1.metric("Sentiment", "0.62")
col2.metric("Predicted Next", f"{df['close'].iloc[-1] * 1.004:,.2f}")
col3.metric("Signal", "BUY")
