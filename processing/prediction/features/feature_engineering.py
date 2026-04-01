from __future__ import annotations

import pandas as pd

from processing.prediction.features.technical_indicators import add_technical_indicators


def make_feature_frame(price_df: pd.DataFrame, sentiment_df: pd.DataFrame | None = None) -> pd.DataFrame:
    frame = add_technical_indicators(price_df)

    frame["return_1"] = frame["close"].pct_change().fillna(0.0)
    frame["return_5"] = frame["close"].pct_change(5).fillna(0.0)
    frame["volatility_20"] = frame["return_1"].rolling(20).std().fillna(0.0)
    frame["target"] = frame["close"].shift(-1)

    if sentiment_df is not None and not sentiment_df.empty:
        sentiment_df = sentiment_df.copy()
        sentiment_df["timestamp"] = pd.to_datetime(sentiment_df["timestamp"], utc=True)
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        merged = pd.merge_asof(
            frame.sort_values("timestamp"),
            sentiment_df.sort_values("timestamp"),
            on="timestamp",
            direction="backward",
        )
        merged["sentiment_score"] = merged.get("sentiment_score", 0.5).fillna(0.5)
        return merged.dropna(subset=["target"])

    frame["sentiment_score"] = 0.5
    return frame.dropna(subset=["target"])


def feature_columns(frame: pd.DataFrame) -> list[str]:
    excluded = {"timestamp", "symbol", "open", "high", "low", "close", "target"}
    return [col for col in frame.columns if col not in excluded]
