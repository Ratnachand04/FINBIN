from __future__ import annotations

import importlib
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.database import db_manager, execute_raw_sql

logger = logging.getLogger(__name__)


class FeatureEngineer:
    def __init__(self) -> None:
        self._np = self._load_optional("numpy")
        self._pd = self._load_optional("pandas")
        self._sklearn_pre = self._load_optional("sklearn.preprocessing")
        self._scaler = self._sklearn_pre.StandardScaler() if self._sklearn_pre else None
        self._feature_importance: dict[str, float] = {}

    def _load_optional(self, module_name: str) -> Any:
        try:
            return importlib.import_module(module_name)
        except Exception:
            logger.warning("Optional dependency unavailable: %s", module_name)
            return None

    async def extract_price_features(self, df: Any) -> Any:
        if self._pd is None or df is None or df.empty:
            return df

        windows = {"1h": 4, "4h": 16, "24h": 96, "7d": 672}
        out = df.copy()
        out["log_return"] = (out["close"] / out["close"].shift(1)).apply(lambda x: self._np.log(x) if x and x > 0 else 0)
        out["hl_range"] = (out["high"] - out["low"]) / out["close"].replace(0, self._np.nan)
        out["co_spread"] = (out["close"] - out["open"]) / out["open"].replace(0, self._np.nan)

        for label, period in windows.items():
            out[f"roc_{label}"] = out["close"].pct_change(periods=period)
            out[f"volatility_{label}"] = out["log_return"].rolling(period).std()
            rolling_mean = out["close"].rolling(period).mean()
            rolling_std = out["close"].rolling(period).std()
            out[f"zscore_{label}"] = (out["close"] - rolling_mean) / rolling_std.replace(0, self._np.nan)

        return out

    async def extract_volume_features(self, df: Any) -> Any:
        if self._pd is None or df is None or df.empty:
            return df

        out = df.copy()
        out["volume_change_rate"] = out["volume"].pct_change()
        out["volume_ma_20"] = out["volume"].rolling(20).mean()
        out["volume_ma_ratio"] = out["volume"] / out["volume_ma_20"].replace(0, self._np.nan)
        out["volume_volatility"] = out["volume"].rolling(20).std()
        out["volume_profile"] = out["volume"] / out["volume"].rolling(96).sum().replace(0, self._np.nan)

        typical_price = (out["high"] + out["low"] + out["close"]) / 3
        money_flow = typical_price * out["volume"]
        pos_flow = money_flow.where(typical_price > typical_price.shift(1), 0).rolling(14).sum()
        neg_flow = money_flow.where(typical_price < typical_price.shift(1), 0).rolling(14).sum().abs()
        mfr = pos_flow / neg_flow.replace(0, self._np.nan)
        out["money_flow_index"] = 100 - (100 / (1 + mfr))
        return out

    async def extract_technical_features(self, df: Any) -> Any:
        if self._pd is None or df is None or df.empty:
            return df

        out = df.copy()
        out["macd_hist_slope"] = out["macd_hist"].diff()
        out["rsi_divergence"] = out.get("rsi_14", 50) - (out["close"].pct_change(14) * 100)
        out["bb_width"] = (out["bb_upper"] - out["bb_lower"]) / out["bb_middle"].replace(0, self._np.nan)
        out["atr_percentile"] = out["atr_14"].rolling(100).rank(pct=True)
        return out

    async def extract_sentiment_features(self, coin: str, timestamp: datetime) -> dict[str, float]:
        symbol = coin.upper()
        end_ts = timestamp
        start_24h = end_ts - timedelta(hours=24)
        start_7d = end_ts - timedelta(days=7)

        async with db_manager.session_factory() as session:
            query = (
                "SELECT "
                "AVG(CASE WHEN ts >= :start_24h THEN sentiment_score END) AS ma_24h, "
                "AVG(CASE WHEN ts >= :start_7d THEN sentiment_score END) AS ma_7d, "
                "STDDEV_POP(CASE WHEN ts >= :start_24h THEN sentiment_score END) AS vol_24h, "
                "AVG(sentiment_score) AS current, "
                "COUNT(*) FILTER (WHERE source_type = 'reddit') AS reddit_count, "
                "COUNT(*) FILTER (WHERE source_type = 'news') AS news_count, "
                "COUNT(*) FILTER (WHERE source_type = 'twitter') AS twitter_count "
                "FROM sentiment_scores WHERE symbol = :symbol AND ts <= :end_ts"
            )
            row = (await execute_raw_sql(
                session,
                query,
                {
                    "symbol": symbol,
                    "end_ts": end_ts,
                    "start_24h": start_24h,
                    "start_7d": start_7d,
                },
            )).first()

            corr_q = (
                "SELECT CORR(s.sentiment_score::float, p.close::float) AS corr "
                "FROM sentiment_scores s "
                "JOIN price_data p ON p.symbol = s.symbol AND DATE_TRUNC('hour', p.ts) = DATE_TRUNC('hour', s.ts) "
                "WHERE s.symbol = :symbol AND s.ts >= :start_7d AND s.ts <= :end_ts"
            )
            corr_row = (await execute_raw_sql(
                session,
                corr_q,
                {"symbol": symbol, "start_7d": start_7d, "end_ts": end_ts},
            )).first()

        current = float(row.current or 0.0) if row else 0.0
        ma_24h = float(row.ma_24h or 0.0) if row else 0.0
        velocity = current - ma_24h
        return {
            "sentiment_current": current,
            "sentiment_ma_24h": ma_24h,
            "sentiment_ma_7d": float(row.ma_7d or 0.0) if row else 0.0,
            "sentiment_volatility": float(row.vol_24h or 0.0) if row else 0.0,
            "sentiment_velocity": velocity,
            "sentiment_price_corr": float(corr_row.corr or 0.0) if corr_row else 0.0,
            "sentiment_src_reddit": float(row.reddit_count or 0) if row else 0.0,
            "sentiment_src_news": float(row.news_count or 0) if row else 0.0,
            "sentiment_src_twitter": float(row.twitter_count or 0) if row else 0.0,
        }

    async def extract_onchain_features(self, coin: str, timestamp: datetime) -> dict[str, float]:
        symbol = coin.upper()
        start_24h = timestamp - timedelta(hours=24)

        async with db_manager.session_factory() as session:
            q = (
                "SELECT "
                "COUNT(*) FILTER (WHERE is_whale = true) AS whale_tx_24h, "
                "COALESCE(SUM(CASE WHEN flow_direction = 'to_exchange' THEN amount_usd ELSE 0 END),0) - "
                "COALESCE(SUM(CASE WHEN flow_direction = 'from_exchange' THEN amount_usd ELSE 0 END),0) AS net_exchange_flow, "
                "COUNT(DISTINCT from_address) + COUNT(DISTINCT to_address) AS active_addresses, "
                "COALESCE(SUM(amount_usd),0) AS tx_volume "
                "FROM onchain_transactions WHERE symbol = :symbol AND ts BETWEEN :start_24h AND :end_ts"
            )
            row = (await execute_raw_sql(
                session,
                q,
                {"symbol": symbol, "start_24h": start_24h, "end_ts": timestamp},
            )).first()

            nvt_q = (
                "SELECT "
                "COALESCE(AVG(p.close), 0) / NULLIF(COALESCE(SUM(o.amount_usd), 0), 0) AS nvt "
                "FROM price_data p "
                "LEFT JOIN onchain_transactions o ON o.symbol = p.symbol "
                "AND DATE_TRUNC('day', o.ts) = DATE_TRUNC('day', p.ts) "
                "WHERE p.symbol = :symbol AND p.ts BETWEEN :start_24h AND :end_ts"
            )
            nvt_row = (await execute_raw_sql(
                session,
                nvt_q,
                {"symbol": symbol, "start_24h": start_24h, "end_ts": timestamp},
            )).first()

        return {
            "whale_tx_24h": float(row.whale_tx_24h or 0) if row else 0.0,
            "net_exchange_flow": float(row.net_exchange_flow or 0.0) if row else 0.0,
            "active_addresses": float(row.active_addresses or 0) if row else 0.0,
            "tx_volume": float(row.tx_volume or 0.0) if row else 0.0,
            "nvt_ratio": float(nvt_row.nvt or 0.0) if nvt_row else 0.0,
        }

    async def extract_temporal_features(self, timestamp: datetime) -> dict[str, float]:
        ts = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=UTC)
        hour = ts.hour
        dow = ts.weekday()

        major_move_ts = await self._last_major_move(ts)
        hours_since_major = (ts - major_move_ts).total_seconds() / 3600.0 if major_move_ts else 0.0

        return {
            "hour_sin": float(self._np.sin(2 * self._np.pi * hour / 24)),
            "hour_cos": float(self._np.cos(2 * self._np.pi * hour / 24)),
            "dow_sin": float(self._np.sin(2 * self._np.pi * dow / 7)),
            "dow_cos": float(self._np.cos(2 * self._np.pi * dow / 7)),
            "is_weekend": float(1.0 if dow >= 5 else 0.0),
            "hours_since_major_move": float(hours_since_major),
        }

    async def create_feature_vector(self, coin: str, timestamp: datetime) -> tuple[Any, list[str]]:
        price_df = await self._load_price_frame(coin, timestamp)
        if price_df is None or price_df.empty:
            raise ValueError(f"No price data available for {coin} at {timestamp}")

        frame = await self.extract_price_features(price_df)
        frame = await self.extract_volume_features(frame)
        frame = await self.extract_technical_features(frame)

        sentiment = await self.extract_sentiment_features(coin, timestamp)
        onchain = await self.extract_onchain_features(coin, timestamp)
        temporal = await self.extract_temporal_features(timestamp)

        latest = frame.iloc[-1].to_dict()
        combined = {**latest, **sentiment, **onchain, **temporal}
        filtered = {k: v for k, v in combined.items() if isinstance(v, (int, float))}

        features_df = self._pd.DataFrame([filtered]).ffill()
        features_df = features_df.fillna(features_df.mean(numeric_only=True)).fillna(0)

        feature_names = list(features_df.columns)
        values = features_df.values
        if self._scaler is not None:
            values = self._scaler.fit_transform(values)

        await self._cache_feature_vector(coin, timestamp, feature_names, values[0].tolist())
        self._update_feature_importance(feature_names)
        return self._np.array(values[0]), feature_names

    async def prepare_training_data(
        self,
        coin: str,
        start_date: datetime,
        end_date: datetime,
    ) -> tuple[Any, Any]:
        frame = await self._load_price_frame_range(coin, start_date, end_date)
        if frame is None or frame.empty:
            raise ValueError("No training data available in requested range")

        frame = await self.extract_price_features(frame)
        frame = await self.extract_volume_features(frame)
        frame = await self.extract_technical_features(frame)

        frame["target"] = (frame["close"].shift(-1) > frame["close"]).astype(int)
        frame = frame.dropna().reset_index(drop=True)

        numeric = frame.select_dtypes(include=["number"]).drop(columns=["target"], errors="ignore")
        numeric = numeric.fillna(method="ffill").fillna(numeric.mean(numeric_only=True)).fillna(0)
        y = frame["target"].values

        seq_len = 60
        X_seq = []
        y_seq = []
        values = numeric.values
        for idx in range(seq_len, len(values)):
            X_seq.append(values[idx - seq_len : idx])
            y_seq.append(y[idx])

        X_arr = self._np.array(X_seq)
        y_arr = self._np.array(y_seq)
        return X_arr, y_arr

    async def _load_price_frame(self, coin: str, timestamp: datetime) -> Any:
        start = timestamp - timedelta(days=7)
        return await self._load_price_frame_range(coin, start, timestamp)

    async def _load_price_frame_range(self, coin: str, start: datetime, end: datetime) -> Any:
        symbol = f"{coin.upper()}USDT"
        async with db_manager.session_factory() as session:
            q = (
                "SELECT ts, open, high, low, close, volume, quote_volume, trade_count, "
                "metadata, interval "
                "FROM price_data "
                "WHERE symbol = :symbol AND ts BETWEEN :start AND :end AND interval = '15m' "
                "ORDER BY ts ASC"
            )
            rows = (await execute_raw_sql(session, q, {"symbol": symbol, "start": start, "end": end})).all()

        if not rows:
            return self._pd.DataFrame()
        return self._pd.DataFrame([dict(row._mapping) for row in rows])

    async def _last_major_move(self, timestamp: datetime) -> datetime | None:
        async with db_manager.session_factory() as session:
            q = (
                "SELECT ts FROM price_data "
                "WHERE interval = '15m' AND ABS((close-open)/NULLIF(open,0)) > 0.03 "
                "AND ts <= :ts ORDER BY ts DESC LIMIT 1"
            )
            row = (await execute_raw_sql(session, q, {"ts": timestamp})).first()
        return row.ts if row else None

    async def _cache_feature_vector(
        self,
        coin: str,
        timestamp: datetime,
        names: list[str],
        values: list[float],
    ) -> None:
        key = f"features:{coin.upper()}:{int(timestamp.timestamp())}"
        try:
            await db_manager.redis_client.set(
                key,
                json.dumps({"names": names, "values": values}),
                ex=3600,
            )
        except Exception as exc:
            logger.warning("Unable to cache feature vector %s: %s", key, exc)

    def _update_feature_importance(self, names: list[str]) -> None:
        for idx, name in enumerate(names):
            self._feature_importance[name] = self._feature_importance.get(name, 0.0) + (1.0 / (idx + 1))

    def get_feature_importance(self, top_n: int = 50) -> dict[str, float]:
        ordered = sorted(self._feature_importance.items(), key=lambda item: item[1], reverse=True)[:top_n]
        return {key: value for key, value in ordered}
