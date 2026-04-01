from __future__ import annotations

import numpy as np
import pandas as pd


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def add_technical_indicators(df: pd.DataFrame) -> pd.DataFrame:
    frame = df.copy()
    close = frame["close"].astype(float)

    delta = close.diff()
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)

    avg_gain = pd.Series(gain, index=frame.index).rolling(14).mean()
    avg_loss = pd.Series(loss, index=frame.index).rolling(14).mean().replace(0, np.nan)
    rs = avg_gain / avg_loss
    frame["rsi_14"] = (100 - (100 / (1 + rs))).fillna(50)

    frame["ema_12"] = _ema(close, 12)
    frame["ema_26"] = _ema(close, 26)
    frame["macd"] = frame["ema_12"] - frame["ema_26"]
    frame["macd_signal"] = _ema(frame["macd"], 9)

    std_20 = close.rolling(20).std().fillna(0)
    sma_20 = close.rolling(20).mean().fillna(close)
    frame["bb_upper"] = sma_20 + (2 * std_20)
    frame["bb_lower"] = sma_20 - (2 * std_20)

    if "volume" in frame:
        vwap_numerator = (frame["close"] * frame["volume"]).cumsum()
        vwap_denominator = frame["volume"].replace(0, np.nan).cumsum()
        frame["vwap"] = (vwap_numerator / vwap_denominator).fillna(frame["close"])

    return frame
