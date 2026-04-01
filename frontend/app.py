from __future__ import annotations

import json
import os
import queue
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

st.set_page_config(
	page_title="BINFIN Dashboard",
	layout="wide",
	initial_sidebar_state="expanded",
)

st.markdown(
	"""
	<style>
		.stApp { background-color: #0f172a; color: #e2e8f0; }
		.block-container { padding-top: 1rem; }
		.metric-box { border: 1px solid #1e293b; border-radius: 12px; padding: 12px; background: #111827; }
		.status-ok { color: #22c55e; font-weight: 700; }
		.status-warn { color: #f59e0b; font-weight: 700; }
		.status-bad { color: #ef4444; font-weight: 700; }
	</style>
	""",
	unsafe_allow_html=True,
)

DEFAULT_COINS = ["BTC", "ETH", "SOL", "ADA", "DOT"]


def _init_state() -> None:
	if "selected_coins" not in st.session_state:
		st.session_state.selected_coins = DEFAULT_COINS
	if "time_range" not in st.session_state:
		st.session_state.time_range = "7d"
	if "refresh_rate" not in st.session_state:
		st.session_state.refresh_rate = 30
	if "notifications_enabled" not in st.session_state:
		st.session_state.notifications_enabled = True
	if "ws_alerts" not in st.session_state:
		st.session_state.ws_alerts = []
	if "ws_started" not in st.session_state:
		st.session_state.ws_started = False


@st.cache_data(ttl=30)
def fetch_data(base_url: str, selected_coins: list[str]) -> dict[str, Any]:
	def _safe_get(path: str, params: dict[str, Any] | None = None) -> Any:
		try:
			response = requests.get(f"{base_url}{path}", params=params, timeout=12)
			response.raise_for_status()
			return response.json()
		except Exception as exc:
			return {"error": str(exc), "path": path}

	coins_upper = [coin.upper() for coin in selected_coins]
	data: dict[str, Any] = {
		"health": _safe_get("/api/v1/health/"),
		"model_runtime": _safe_get("/api/v1/model/runtime"),
		"signals": _safe_get("/api/v1/signals/", {"page_size": 200}),
		"active_signals": _safe_get("/api/v1/signals/active", {"limit": 200}),
		"sentiment": _safe_get("/api/v1/sentiment/", {"hours": 48, "limit": 500}),
		"predictions": _safe_get("/api/v1/predictions/", {"hours": 48, "limit": 500}),
		"recent_outcomes": _safe_get("/api/v1/signals/recent-outcomes"),
		"performance": _safe_get("/api/v1/signals/performance", {"group_by": "coin"}),
		"coins": {},
	}

	for coin in coins_upper:
		data["coins"][coin] = {
			"details": _safe_get(f"/api/v1/coins/{coin}"),
			"price_history": _safe_get(f"/api/v1/coins/{coin}/price-history", {"interval": "15m", "limit": 300}),
			"sentiment_history": _safe_get(f"/api/v1/coins/{coin}/sentiment-history", {"window": "24h", "days": 14}),
			"indicators": _safe_get(f"/api/v1/coins/{coin}/technical-indicators"),
		}
	return data


def run_finance_news_training(base_url: str, symbols: list[str], interval: str, max_rows: int) -> dict[str, Any]:
	payload = {
		"symbols": [symbol.upper() for symbol in symbols],
		"interval": interval,
		"max_rows_per_symbol": max_rows,
		"sentiment_sample_size": 30,
	}
	response = requests.post(f"{base_url}/api/v1/model/train-finance-news", json=payload, timeout=600)
	response.raise_for_status()
	return response.json()


def render_price_chart(coin: str, data: list[dict[str, Any]]) -> None:
	if not data:
		st.info(f"No price data for {coin}")
		return

	frame = pd.DataFrame(data)
	if frame.empty:
		st.info(f"No price data for {coin}")
		return

	frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
	frame = frame.sort_values("ts")

	fig = go.Figure()
	fig.add_trace(
		go.Candlestick(
			x=frame["ts"],
			open=frame["open"],
			high=frame["high"],
			low=frame["low"],
			close=frame["close"],
			name="OHLC",
		)
	)
	fig.add_trace(
		go.Bar(
			x=frame["ts"],
			y=frame["volume"],
			name="Volume",
			marker_color="#334155",
			yaxis="y2",
			opacity=0.3,
		)
	)

	fig.update_layout(
		title=f"{coin} Price Action",
		template="plotly_dark",
		xaxis_rangeslider_visible=False,
		yaxis=dict(title="Price"),
		yaxis2=dict(title="Volume", overlaying="y", side="right", showgrid=False),
		legend=dict(orientation="h"),
		height=460,
	)
	st.plotly_chart(fig, use_container_width=True)


def render_sentiment_heatmap(coins: list[str], sentiments: list[dict[str, Any]]) -> None:
	if not sentiments:
		st.info("No sentiment data")
		return

	frame = pd.DataFrame(sentiments)
	if frame.empty:
		st.info("No sentiment data")
		return

	frame["symbol"] = frame["symbol"].astype(str).str.upper()
	frame = frame[frame["symbol"].isin([coin.upper() for coin in coins])]
	if frame.empty:
		st.info("No sentiment data for selected coins")
		return

	agg = frame.groupby("symbol", as_index=False)["sentiment_score"].mean()
	agg["x"] = "Sentiment"
	fig = px.density_heatmap(
		agg,
		x="x",
		y="symbol",
		z="sentiment_score",
		color_continuous_scale=["#ef4444", "#facc15", "#22c55e"],
		text_auto=".2f",
		title="Sentiment Heatmap",
	)
	fig.update_layout(template="plotly_dark", height=330)
	st.plotly_chart(fig, use_container_width=True)


def render_signals_table(signals: list[dict[str, Any]]) -> None:
	if not signals:
		st.info("No signals found")
		return

	frame = pd.DataFrame(signals)
	if frame.empty:
		st.info("No signals found")
		return

	show_cols = [
		col
		for col in ["ts", "symbol", "signal", "strength", "confidence", "entry_price", "take_profit", "stop_loss", "rationale"]
		if col in frame.columns
	]
	frame = frame[show_cols].copy()
	if "ts" in frame.columns:
		frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
	if "rationale" in frame.columns:
		frame["rationale"] = frame["rationale"].astype(str).str.slice(0, 110)

	def _row_style(row: pd.Series) -> list[str]:
		signal = str(row.get("signal", "")).upper()
		if signal == "BUY":
			return ["background-color: rgba(34,197,94,0.15)"] * len(row)
		if signal == "SELL":
			return ["background-color: rgba(239,68,68,0.15)"] * len(row)
		return [""] * len(row)

	st.dataframe(frame.style.apply(_row_style, axis=1), use_container_width=True, height=320)


def render_backtest_results(results: dict[str, Any]) -> None:
	if not results or isinstance(results, list):
		st.info("No backtest results")
		return

	col1, col2, col3 = st.columns(3)
	col1.metric("Run Count", int(results.get("run_count") or 0))
	col2.metric("Avg Return", f"{float(results.get('avg_return') or 0.0):.2f}%")
	col3.metric("Avg Sharpe", f"{float(results.get('avg_sharpe') or 0.0):.2f}")

	points = pd.DataFrame(
		{
			"metric": ["avg_return", "best_return", "avg_sharpe", "avg_drawdown"],
			"value": [
				float(results.get("avg_return") or 0.0),
				float(results.get("best_return") or 0.0),
				float(results.get("avg_sharpe") or 0.0),
				float(results.get("avg_drawdown") or 0.0),
			],
		}
	)
	fig = px.bar(points, x="metric", y="value", title="Backtest Summary", template="plotly_dark")
	st.plotly_chart(fig, use_container_width=True)


def _start_ws_listener(ws_url: str) -> queue.Queue[str]:
	events: queue.Queue[str] = queue.Queue()

	def _runner() -> None:
		try:
			import websocket  # type: ignore

			ws = websocket.WebSocket()
			ws.connect(ws_url, timeout=6)
			while True:
				msg = ws.recv()
				events.put(str(msg))
		except Exception as exc:
			events.put(f"ws_error:{exc}")

	thread = threading.Thread(target=_runner, daemon=True)
	thread.start()
	return events


_init_state()

with st.sidebar:
	st.header("Controls")
	st.session_state.selected_coins = st.multiselect(
		"Coins",
		options=DEFAULT_COINS,
		default=st.session_state.selected_coins,
	)
	st.session_state.time_range = st.selectbox("Time Range", ["24h", "7d", "30d"], index=["24h", "7d", "30d"].index(st.session_state.time_range))

	st.subheader("Settings")
	st.session_state.refresh_rate = st.slider("Refresh rate (seconds)", min_value=10, max_value=120, step=5, value=st.session_state.refresh_rate)
	st.session_state.notifications_enabled = st.toggle("Enable notifications", value=st.session_state.notifications_enabled)

	backend_base = st.text_input("Backend URL", value=os.getenv("BACKEND_URL", "http://localhost:8000"))
	ws_url = st.text_input("WebSocket URL", value=os.getenv("WS_SIGNALS_URL", "ws://localhost:8000/api/v1/signals/ws/notifications"))

	health = fetch_data(backend_base, st.session_state.selected_coins).get("health", {})
	status = health.get("status", "unknown") if isinstance(health, dict) else "unknown"
	css_class = "status-ok" if status == "ok" else "status-warn" if status == "degraded" else "status-bad"
	st.markdown(f"Connection: <span class='{css_class}'>{status.upper()}</span>", unsafe_allow_html=True)

if st.session_state.notifications_enabled and not st.session_state.ws_started:
	st.session_state.ws_queue = _start_ws_listener(ws_url)
	st.session_state.ws_started = True

if st.session_state.notifications_enabled and "ws_queue" in st.session_state:
	while not st.session_state.ws_queue.empty():
		raw_msg = st.session_state.ws_queue.get_nowait()
		st.session_state.ws_alerts.insert(0, raw_msg)
		st.session_state.ws_alerts = st.session_state.ws_alerts[:30]

data = fetch_data(backend_base, st.session_state.selected_coins)
active_signals = data.get("active_signals", []) if isinstance(data.get("active_signals"), list) else []
recent_outcomes = data.get("recent_outcomes", []) if isinstance(data.get("recent_outcomes"), list) else []
performance = data.get("performance", {}) if isinstance(data.get("performance"), dict) else {}

st.title("BINFIN Trading Intelligence Dashboard")
st.caption(f"Last refresh: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}")

metric1, metric2, metric3, metric4 = st.columns(4)
metric1.metric("Total Active Signals", len(active_signals))

wins = 0
total = 0
best_symbol = "N/A"
best_return = -9999.0
for item in recent_outcomes:
	pnl = float(item.get("pnl_pct") or 0.0)
	total += 1
	if pnl > 0:
		wins += 1
	if pnl > best_return:
		best_return = pnl
		best_symbol = str(item.get("symbol", "N/A"))

metric2.metric("Win Rate (30d)", f"{(wins / total * 100) if total else 0.0:.1f}%")
metric3.metric("Best Performer", f"{best_symbol} ({best_return:.2f}%)" if total else "N/A")
health_state = data.get("health", {}).get("status", "unknown") if isinstance(data.get("health"), dict) else "unknown"
metric4.metric("System Status", str(health_state).upper())

tab_live, tab_signal, tab_market, tab_backtest, tab_model = st.tabs(
	["Live Dashboard", "Signal Analysis", "Market Overview", "Backtesting", "Model Performance"]
)

with tab_live:
	st.subheader("Real-Time Tickers")
	ticker_cols = st.columns(max(1, len(st.session_state.selected_coins)))
	for idx, coin in enumerate(st.session_state.selected_coins):
		details = data.get("coins", {}).get(coin, {}).get("details", {})
		if not isinstance(details, dict):
			details = {}
		price = float(details.get("current_price") or 0.0)
		change = float(details.get("change_24h_pct") or 0.0)
		ticker_cols[idx].metric(f"{coin}", f"{price:,.4f}", f"{change:.2f}%")

	col_a, col_b = st.columns([2, 3])
	with col_a:
		sentiment_rows = data.get("sentiment", []) if isinstance(data.get("sentiment"), list) else []
		render_sentiment_heatmap(st.session_state.selected_coins, sentiment_rows)
	with col_b:
		st.subheader("Active Signals")
		render_signals_table(active_signals)

	st.subheader("Recent Alerts")
	alerts = st.session_state.ws_alerts[:10] if st.session_state.notifications_enabled else []
	if alerts:
		for msg in alerts:
			st.code(str(msg), language="json")
	else:
		st.info("No recent alerts")

with tab_signal:
	st.subheader("Signal History")
	signals = data.get("signals", []) if isinstance(data.get("signals"), list) else []
	render_signals_table(signals)

	perf_rows = performance.get("rows", []) if isinstance(performance, dict) else []
	if perf_rows:
		perf_df = pd.DataFrame(perf_rows)
		fig_perf = px.bar(perf_df, x="group", y="win_rate", color="avg_return", title="Signal Performance by Coin", template="plotly_dark")
		st.plotly_chart(fig_perf, use_container_width=True)

	selected_signal_id = st.number_input("Select signal ID for explanation", min_value=0, step=1, value=0)
	if selected_signal_id > 0:
		try:
			detail = requests.get(f"{backend_base}/api/v1/signals/{int(selected_signal_id)}", timeout=10).json()
			st.json(detail)
		except Exception as exc:
			st.warning(f"Unable to load signal detail: {exc}")

with tab_market:
	selected_coin = st.selectbox("Coin for market view", st.session_state.selected_coins)
	coin_data = data.get("coins", {}).get(selected_coin, {})
	render_price_chart(selected_coin, coin_data.get("price_history", []) if isinstance(coin_data, dict) else [])

	if isinstance(coin_data, dict):
		st.subheader("Technical Indicators")
		indicators = coin_data.get("indicators", {})
		if isinstance(indicators, dict):
			st.json(indicators)

		st.subheader("Sentiment Timeline")
		sentiment_history = coin_data.get("sentiment_history", [])
		if isinstance(sentiment_history, list) and sentiment_history:
			s_df = pd.DataFrame(sentiment_history)
			s_df["ts"] = pd.to_datetime(s_df["ts"], utc=True)
			fig_sent = px.line(s_df, x="ts", y="avg_sentiment", template="plotly_dark", title="Sentiment Over Time")
			st.plotly_chart(fig_sent, use_container_width=True)

with tab_backtest:
	st.subheader("Backtest Configuration")
	c1, c2, c3 = st.columns(3)
	bt_coin = c1.selectbox("Coin", st.session_state.selected_coins)
	bt_capital = c2.number_input("Initial Capital", min_value=100.0, value=10000.0, step=100.0)
	bt_days = c3.slider("Lookback Days", min_value=7, max_value=365, value=90)

	if st.button("Run Backtest", use_container_width=True):
		start_date = (datetime.now(UTC) - timedelta(days=bt_days)).isoformat()
		end_date = datetime.now(UTC).isoformat()
		payload = {
			"start_date": start_date,
			"end_date": end_date,
			"coins": [bt_coin],
			"strategy_config": {"initial_capital": bt_capital},
		}
		with st.spinner("Running backtest..."):
			try:
				result = requests.post(f"{backend_base}/api/v1/backtest/run", json=payload, timeout=120).json()
				st.session_state.backtest_result = result
			except Exception as exc:
				st.error(f"Backtest failed: {exc}")

	if "backtest_result" in st.session_state:
		render_backtest_results(st.session_state.backtest_result)

	recent_summary = {}
	try:
		recent_summary = requests.get(f"{backend_base}/api/v1/backtest/runs/recent-summary", timeout=10).json()
	except Exception:
		pass
	render_backtest_results(recent_summary if isinstance(recent_summary, dict) else {})

with tab_model:
	st.subheader("Model Metrics Comparison")
	runtime = data.get("model_runtime", {}) if isinstance(data.get("model_runtime"), dict) else {}
	if runtime and not runtime.get("error"):
		col_r1, col_r2, col_r3 = st.columns(3)
		col_r1.metric("Runtime Device", str(runtime.get("selected_device", "cpu")).upper())
		col_r2.metric("CUDA Available", "YES" if runtime.get("cuda_available") else "NO")
		col_r3.metric("Ollama Reachable", "YES" if runtime.get("ollama_reachable") else "NO")
		gpu_name = runtime.get("gpu_name")
		if gpu_name:
			st.caption(f"GPU: {gpu_name}")

	st.subheader("Finance/News Sentiment Retraining")
	train_cols = st.columns(3)
	train_symbols = train_cols[0].multiselect(
		"Symbols",
		options=["BTCUSDT", "ETHUSDT"],
		default=["BTCUSDT", "ETHUSDT"],
		key="model_train_symbols",
	)
	train_interval = train_cols[1].selectbox("Interval", ["15m", "1h", "4h", "1d"], index=0, key="model_train_interval")
	train_rows = train_cols[2].slider("Rows per symbol", min_value=1000, max_value=20000, value=6000, step=500, key="model_train_rows")

	if st.button("Run Finance/News Sentiment + Train", use_container_width=True):
		if not train_symbols:
			st.warning("Select at least one symbol")
		else:
			with st.spinner("Running sentiment analysis with Ollama and training prediction models..."):
				try:
					result = run_finance_news_training(backend_base, train_symbols, train_interval, train_rows)
					st.session_state.model_train_result = result
				except Exception as exc:
					st.error(f"Training request failed: {exc}")

	if "model_train_result" in st.session_state:
		result = st.session_state.model_train_result
		st.success("Training completed")
		run_runtime = result.get("runtime", {}) if isinstance(result, dict) else {}
		if run_runtime:
			st.caption(
				f"Runtime: {str(run_runtime.get('selected_device', 'cpu')).upper()} | "
				f"CUDA: {'YES' if run_runtime.get('cuda_available') else 'NO'} | "
				f"Ollama: {'YES' if run_runtime.get('ollama_reachable') else 'NO'}"
			)

		rows = result.get("results", []) if isinstance(result, dict) else []
		if isinstance(rows, list) and rows:
			summary = []
			for item in rows:
				sentiment = item.get("sentiment", {}) if isinstance(item, dict) else {}
				summary.append(
					{
						"symbol": item.get("symbol"),
						"status": item.get("status"),
						"trained_rows": item.get("trained_rows"),
						"current_price": item.get("current_price"),
						"predicted_price": item.get("predicted_price"),
						"low_95": item.get("low_95"),
						"high_95": item.get("high_95"),
						"sentiment_score": sentiment.get("aggregate_score"),
						"sentiment_samples": sentiment.get("sample_count"),
					}
				)
			st.dataframe(pd.DataFrame(summary), use_container_width=True, height=240)

			for item in rows:
				if not isinstance(item, dict):
					continue
				sentiment = item.get("sentiment", {}) if isinstance(item.get("sentiment"), dict) else {}
				if sentiment:
					with st.expander(f"Sentiment details - {item.get('symbol', 'N/A')}"):
						st.json(sentiment)

	preds = data.get("predictions", []) if isinstance(data.get("predictions"), list) else []
	if preds:
		p_df = pd.DataFrame(preds)
		p_df["ts"] = pd.to_datetime(p_df["ts"], utc=True)
		fig_acc = px.line(p_df.sort_values("ts"), x="ts", y="confidence", color="symbol", template="plotly_dark", title="Prediction Confidence Over Time")
		st.plotly_chart(fig_acc, use_container_width=True)

		fig_horizon = px.histogram(p_df, x="prediction_horizon", color="symbol", barmode="group", template="plotly_dark", title="Predictions by Horizon")
		st.plotly_chart(fig_horizon, use_container_width=True)
	else:
		st.info("No prediction data available")

	perf_rows = performance.get("rows", []) if isinstance(performance, dict) else []
	if perf_rows:
		perf_df = pd.DataFrame(perf_rows)
		fig_feat = px.scatter(perf_df, x="avg_return", y="win_rate", size="total_trades", color="group", template="plotly_dark", title="Model Outcome Landscape")
		st.plotly_chart(fig_feat, use_container_width=True)

st.caption("Auto-refresh enabled. Update interval controlled in sidebar settings.")
if st.session_state.refresh_rate > 0:
	try:
		st.autorefresh(interval=st.session_state.refresh_rate * 1000, key="binfin-refresh")
	except Exception:
		try:
			from streamlit_autorefresh import st_autorefresh  # type: ignore

			st_autorefresh(interval=st.session_state.refresh_rate * 1000, key="binfin-refresh")
		except Exception:
			pass
