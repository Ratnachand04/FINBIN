from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from processing.prediction.features.feature_engineering import feature_columns, make_feature_frame
from processing.prediction.models.ensemble_model import EnsembleModel
from processing.prediction.models.lstm_model import LSTMModel
from processing.prediction.models.prophet_model import ProphetModel
from processing.prediction.models.xgboost_model import XGBoostModel


@dataclass
class PredictionOutput:
    symbol: str
    next_price: float
    low_95: float
    high_95: float


class PredictionEngine:
    def __init__(self) -> None:
        self.prophet = ProphetModel()
        self.xgb = XGBoostModel()
        self.lstm = LSTMModel()
        self.ensemble = EnsembleModel()

    def train_and_predict(
        self,
        symbol: str,
        price_df: pd.DataFrame,
        sentiment_df: pd.DataFrame | None = None,
    ) -> PredictionOutput:
        frame = make_feature_frame(price_df, sentiment_df)
        cols = feature_columns(frame)

        self.prophet.train(price_df)
        self.xgb.train(frame, cols)
        self.lstm.train(price_df)

        p = self.prophet.predict_next(price_df)
        x = self.xgb.predict_next(frame, cols)
        l = self.lstm.predict_next(price_df)
        e = self.ensemble.combine(p.yhat, p.yhat_lower, p.yhat_upper, x, l)

        return PredictionOutput(symbol=symbol.upper(), next_price=e.value, low_95=e.low, high_95=e.high)
