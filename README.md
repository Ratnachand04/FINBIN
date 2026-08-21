# Crypto Intelligence Terminal

Self-hosted crypto trading intelligence system using open-source LLMs to analyze sentiment, predict prices, and generate actionable trading signals.

> **Measured results:** [Performance Evaluation](#performance-evaluation) &nbsp;|&nbsp;
> **Reproduce:** `python scripts/evaluate_walkforward.py` &nbsp;|&nbsp;
> **Verify the backtester:** `python scripts/null_test_backtest.py` &nbsp;|&nbsp;
> **Paper:** [`paper/crypto_direction_evaluation.tex`](paper/crypto_direction_evaluation.tex)

## Model Intro

The **Crypto Intelligence Terminal** is a robust, hybrid-compute AI pipeline for cryptocurrency sentiment analysis and price prediction. It operates as a self-hosted platform running locally to maintain full data privacy and control. By leveraging both traditional quantitative modeling techniques (XGBoost, Prophet) and state-of-the-art Generative AI (Mistral-7B via QLoRA fine-tuning), the system provides an end-to-end framework for making informed, data-driven trading decisions.

## Features of this Model

- **Real-time data collection:** Pulls continuous streams of information from Reddit, News APIs, On-chain (Etherscan), and Price feeds (Binance).
- **AI-powered sentiment analysis:** Utilizes locally hosted Ollama Mistral-7B alongside FinBERT for deep contextual analysis of market news.
- **Multi-model price prediction:** Incorporates financial modeling techniques like Prophet, LSTM, and XGBoost.
- **Intelligent signal generation:** Produces definitive BUY/SELL/HOLD signals backed by explainability metrics.
- **Comprehensive backtesting engine:** Validates the historical accuracy of deployed models using the Sharpe Ratio and drawdown metrics.
- **Unified Graphical Dashboards:** Provides a full animated Web dashboard (HTML/CSS/JS + Lightweight Charts) and CLI-based rich dashboards.
- **Open-source architecture:** Fully containerized for self-hosted, independent operation without reliance on expensive third-party foundational models.

## Architecture

```mermaid
flowchart TD
    subgraph DataSources["Data Sources (Free APIs)"]
        direction LR
        A["Binance API\nPrice OHLCV"] 
        B["NewsAPI\nCrypto News"]
        C["Reddit API\nr/cryptocurrency"]
        D["Etherscan API\nWhale Txns"]
    end

    subgraph Backend["Python Backend (FastAPI)"]
        E["Data Ingestion\nPipeline"]
        F[("PostgreSQL +\nTimescaleDB")]
        G["Sentiment Engine\nFinBERT"]
        H["Price Prediction\nProphet + XGBoost"]
        I["Signal Generator\nBUY/SELL/HOLD"]
        J["Backtesting Engine\nSharpe Ratio"]
        K["Ollama/Mistral 7B\nAI Insights"]

        E --> F
        F --> G
        F --> H
        G --> I
        H --> I
        I --> J
    end

    subgraph Frontend["Frontend Dashboard"]
        L["HTML/CSS/JS\nApexCharts"]
    end

    A --> E
    B --> E
    C --> E
    D --> E

    F --> L
    G --> L
    H --> L
    I --> L
    J --> L
    K --> L
```

## Requirements Needed

- **Python:** 3.9+ 
- **Containerization:** Docker and Docker Compose
- **Memory Minimum:** 16GB System RAM
- **GPU Minimum (Optional but Recommended):** 8GB GPU VRAM (NVIDIA) for heavy QLoRA model training and hardware-accelerated instance generation.

## Single Command Deployment

To set up and run the system locally, clone the repository, configure your API keys, and launch the Docker cluster. 

```bash
git clone https://github.com/Ratnachand04/FINBIN.git
cd FINBIN
cp .env.example .env

# Don't forget to edit the .env to configure your specific API keys
```

### API Keys Configuration

To fetch real-world data, the system requires API keys. You have two options to configure them:
1. **Locally via the `.env` file:** Copy `.env.example` to `.env` and insert your keys (e.g., `BINANCE_API_KEY`, `NEWS_API_KEY`). The backend services will load them securely on startup.
2. **Via the Frontend Dashboard:** Once deployed, navigate to `http://localhost:8501`. The dashboard opens directly (no username/password prompt) and streams live backend data.

### Run using Docker Compose

If you have standard docker compose installed, run:

```bash
docker-compose up -d --build
```

*(Alternatively, use the built-in deployment scripts for automatic environment checking: `./scripts/deploy_model.ps1` on Windows or `./scripts/deploy_model.sh` on Linux/macOS)*

## Teardown and Cleanup Commands

When you need to stop the models and safely remove the configuration, use the following real operational commands:

**1. Down the containers natively:**
```bash
docker-compose down
```

**2. Down the containers and remove all local generated images and database volumes (Full Reset):**
```bash
docker-compose down --rmi all -v
```

## How the Model Uses Data to Predict 

This intelligence system relies on a multi-modal approach to forecasting crypto-asset trends:

- **What it consumes:** The engine ingests time-series **price/volume data** (OHLCV metrics from Binance), **on-chain activity metrics** (massive whale transactions from Etherscan), and fundamental **market narratives** (news articles and Reddit threads). 
- **How it processes the context:** Traditional numerical indicators (like Moving Averages and RSI) are generated from the OHLCV data. Simultaneously, Mistral-7B and FinBERT read the unstructured textual feeds to compute an overarching *Bullish/Bearish Sentiment Score*.
- **What it predicts:** It projects short-to-medium-term price trajectories. The quantitative models (Prophet/XGBoost) recognize historical price patterns, while the AI models identify periods of market euphoria or panic.
- **The Final Output:** These diverse dimensions are synthesized to issue clear **BUY, SELL, or HOLD** signals alongside a "confidence" metric, explaining the narrative reasoning behind the system's choice.

## Working Process

1. **Continuous Data Ingestion:** The data ingestion pipeline operates continuously using your configured API keys, aggregating the latest daily price OHLCV data, crypto news, whale transactions, and Reddit posts into the PostgreSQL database.
2. **AI Inference & Sentiment Filtering:** The backend processes the textual and numerical data using FinBERT and an Ollama-powered Mistral-7B runtime to extract meaningful, contextual market sentiment from the raw pipeline.
3. **Price Prediction Pipeline:** Dedicated quantitative models (Prophet, XGBoost) concurrently utilize the historical time-series data to analyze and project impending price trends.
4. **Signal Aggregation:** The Signal Generator cross-references the processed sentiment data with the predictive numeric modeling to produce actionable BUY/HOLD/SELL signals. The backtesting engine then appraises these indications.
5. **Insights Presentation:** The unified frontend (developed with HTML/CSS/JS and Lightweight Charts) surfaces these indicators in a live animated dashboard.

## GPU and CPU Edition 

The architecture supports a dual-pronged **Split Runtime Deployment**, carefully balancing GPU vs. CPU resources to achieve peak operational efficiency:

- **Automatic GPU First:** By default, the environment attempts GPU deployment by leveraging CUDA extensions mapped in `docker-compose.yml` (and `docker-compose.gpu.yml` for dedicated fallback scripts).
- **GPU-CPU Workload Splitting:** Generative inferences utilizing Mistral-7B automatically allocate into the GPU-enabled `ollama` container to process tokens rapidly. Conversely, RAG (Retrieval-Augmented Generation) context retrieval and parsing isolate entirely to the CPU (`RAG_CONTEXT_CPU_ONLY=true`) in the backend. 
- **Automated CPU Fallback:** The backend performs API verification locally on initialization (`/api/v1/model/runtime`). If an incompatible CUDA runtime is identified—or if VRAM is fully constrained during deployment—the system automatically falls back and restarts the inference containers on your local CPU cores. 
- **Manual Mode Operation:** At any time, you can force purely CPU-based LoRA fine-tuning workflows via deployment flags (e.g., `-FineTuneTrainerMode cpu-lora`), dropping 4-bit quantization to ensure platform stability on machines lacking dedicated GPUs.

## Mistral Model Optimizations

To ensure the large language model (Mistral-7B) runs efficiently on consumer or mid-tier hardware, the following optimizations are natively integrated:

- **4-Bit Quantization (QLoRA):** The base Mistral model has been fully quantized to 4-bit precision using the bitsandbytes library. This dramatically decreases the required GPU VRAM for both inference and continuous fine-tuning without sacrificing context reasoning.
- **Low-Rank Adaptation (LoRA):** Rather than updating all 7-billion parameters, our local trainer scripts inject small, trainable rank decomposition matrices. This targets only the weights necessary for financial sentiment interpretation, compounding training speeds exponentially.
- **Split Workload RAG:** The heavy generative text-streaming task is strictly pinned to the GPU via Ollama, while Retrieval-Augmented Generation retrieval operations (vector embeddings, database routing) are purposely offloaded to the CPU. This cleanly preserves scarce GPU memory.

## Performance Evaluation

Reproduce with `python scripts/evaluate_walkforward.py`. Full output in
[`docs/evaluation_results.json`](docs/evaluation_results.json).

**Headline: the directional signal is statistically significant and economically
marginal.** It predicts next-day direction at 52.9% against a 50% null
(p < 0.0001), and it breaks even at 10.7 bps per side against real costs of
roughly 9 bps. That margin is too thin to trade. Both halves of that sentence
are the result; reporting only the first would be misleading.

### Setup

| | |
|---|---|
| Data | Binance daily OHLCV. BTC/ETH from 2017-10-16, DOGE from its 2019-09-03 listing, all through 2026-03-31 (3,089 / 3,089 / 2,402 usable bars after the 60-day feature warm-up) |
| Validation | Expanding-window walk-forward: 1,000-day initial train, 250-day test folds, **5-day embargo** between folds. 9 folds for BTC/ETH, 6 for DOGE |
| Out-of-sample predictions | 5,565 (2,084 BTC / 2,084 ETH / 1,397 DOGE) |
| Features | 34 causal features: multi-horizon log returns, realised vol, z-scored volume and trade count, taker-buy ratio, average trade size, RSI, MACD histogram, Bollinger position, ATR, day-of-week |
| Preprocessing | `StandardScaler` fitted **inside each fold on training rows only** |
| Costs | 4 bps taker fee + 5 bps slippage, per side |

### Directional accuracy (out-of-sample)

| Symbol | Model | n | Accuracy | 95% CI | p vs 50% | Gross Sharpe |
|---|---|---:|---:|---|---:|---:|
| BTC | Logistic | 2,084 | 52.74% | [50.6%, 54.9%] | 0.013 | 0.44 |
| BTC | GBDT | 2,084 | 52.11% | [50.0%, 54.3%] | 0.054 | -0.00 |
| ETH | Logistic | 2,084 | 53.07% | [50.9%, 55.2%] | 0.005 | 0.41 |
| ETH | GBDT | 2,084 | 51.34% | [49.2%, 53.5%] | 0.220 | 0.36 |
| DOGE | Logistic | 1,397 | 53.04% | [50.4%, 55.7%] | 0.023 | 0.61 |
| DOGE | GBDT | 1,397 | 51.90% | [49.3%, 54.5%] | 0.156 | 0.39 |
| **Pooled** | **Logistic** | **5,565** | **52.94%** | **[51.6%, 54.3%]** | **<0.0001** (z=4.38) | **0.60** |
| Pooled | GBDT | 5,565 | 51.77% | [50.5%, 53.1%] | 0.008 (z=2.64) | — |

Baselines over the same windows:

| Baseline | BTC | ETH | DOGE |
|---|---:|---:|---:|
| Persistence (tomorrow repeats today) | 46.98% | 47.46% | 46.89% |
| Majority class | 50.53% | 49.62% | 51.68% |
| Buy-and-hold annualised Sharpe | 0.89 | 0.89 | 0.49 |

The regularised linear model beats the gradient-boosted trees on every symbol —
consistent with a weak, close-to-linear signal and a low signal-to-noise ratio,
where the flexible model spends its capacity on noise.

### Cost sensitivity

Equal-weight daily long/short book on the logistic signal. Average daily
turnover 0.774.

| Cost per side (bps) | 0 | 2 | 5 | **9** | 15 | 20 |
|---|---:|---:|---:|---:|---:|---:|
| Annualised Sharpe | 0.60 | 0.49 | 0.32 | **0.10** | -0.24 | -0.52 |

**Breakeven: 10.74 bps per side.** Actual assumed cost is 9 bps, so the strategy
sits just inside breakeven — within the error bar of zero.

### End-to-end backtest

Running the same signals through the corrected engine, which imposes a realistic
one-bar implementation lag (signal at close of day *t*, entry at open of *t+1*,
exit at close of *t+2*):

| Metric | Value |
|---|---:|
| Trades | 5,562 |
| Win rate | 50.02% |
| Total return | -70.45% |
| Max drawdown | 72.73% |
| **Sharpe (net)** | **-0.346** |
| Profit factor | 0.920 |

The gap between +0.10 (cost model, no lag) and -0.35 (engine, with lag) is the
cost of implementation delay: the edge lives in the close-to-close window the
label describes, and a one-bar delay spends most of it. That gap is a finding,
not a discrepancy.

### Honest conclusions

1. There is weak but real short-horizon directional structure in daily crypto
   returns, detectable at p < 0.0001 over 5,565 out-of-sample predictions.
2. It does not survive transaction costs at daily frequency.
3. It does not beat buy-and-hold on a risk-adjusted basis (0.60 gross vs 0.89).
4. Capturing it would require either lower costs (maker rebates, sub-1bp
   execution) or a shorter horizon where the signal has not yet decayed.

### Excluded: the simulated sentiment feature

`data_ingestion/output/sentiment/*.csv` is **not** used in this evaluation.
Those files are generated by
[`data_ingestion/scripts/fetch_sentiment_data.py`](data_ingestion/scripts/fetch_sentiment_data.py)
as `clip(same_day_return * 15, -1, 1) + uniform(-0.2, 0.2)`, with a headline
drawn from 15 fixed templates. Measured against BTC daily bars:

| Relationship | Correlation | Sign agreement |
|---|---:|---:|
| `sentiment_score` vs **same-day** return | **0.916** | 87.0% |
| `sentiment_score` vs **next-day** return | -0.018 | 47.2% |

It is a re-encoding of the same day's label with noise, carrying no forward
information. Including it as a predictor of same-day direction would produce a
large and entirely spurious accuracy. Any sentiment claim requires real
timestamped text, which this dataset does not contain.

### What an earlier revision got wrong

A previous version of this README published Sharpe ratios above 20. Those came
from the defects below, all of which inflated or corrupted reported performance.
Each is now pinned by a regression test in
[`tests/test_backtest_engine_correctness.py`](tests/test_backtest_engine_correctness.py)
and [`tests/test_feature_pipeline_correctness.py`](tests/test_feature_pipeline_correctness.py).

| Defect | Effect on reported numbers |
|---|---|
| Open positions were priced from the *incoming signal's* symbol, not their own | In multi-asset runs, a BTC position could be stopped out at DOGE's price |
| Price lookup selected the nearest bar by absolute time distance | Fills could resolve against a bar that had not occurred yet (look-ahead) |
| Sharpe annualised per-trade returns by `sqrt(252)` | Trades do not arrive on a fixed clock; the factor was arbitrary. Source of the >20 Sharpe values |
| Short positions marked to market as `+price x qty` | A short *gained* equity as the price rose, corrupting the equity curve and every metric derived from it |
| Stops and targets evaluated only when a new signal arrived, and only against `close` | Intrabar stop hits were never simulated |
| Monte Carlo shuffled the P&L list and summed it | Summation is permutation-invariant, so all simulated paths were identical, the confidence interval collapsed to a point, and P(profit) was always 0 or 1 |
| Calmar divided return by the single worst *trade* | Calmar is defined against peak-to-trough drawdown of the equity curve |
| Trainer reported `sharpe_ratio` and `profit_factor` built from +/-1 per correct prediction | Both are deterministic transforms of accuracy (`mean = 2*acc - 1`, `PF = acc/(1-acc)`), not risk or P&L measures |
| `900 rows per symbol/interval` applied uniformly across timeframes | 900 15m bars is 9.4 days of data — too short to support any claim |
| `StandardScaler().fit_transform()` called on a single row at inference | One sample per column has zero variance, so every served feature vector was identically 0.0 |
| Train/validation split had no embargo, with 60-step windows and 672-bar rolling features | Adjacent folds shared underlying bars; validation partly measured training data |
| Missing values imputed with the mean of the full frame | Future rows leaked into past ones through the imputation statistic |

### Methodology

- **Fills.** A signal computed from the close of bar *i* is executed at the open
  of bar *i+1*. No fill can reference a bar at or after its own execution bar.
- **Exits.** Stop and target are checked against each bar's high/low. When one
  bar touches both levels, the stop is assumed to have filled first, since OHLC
  data cannot resolve the ordering.
- **Costs.** 4bp per side (Binance spot taker) plus 5bp slippage per side,
  charged on both legs.
- **Sharpe.** Computed from equity-curve returns sampled at the bar cadence, with
  the annualisation factor inferred from the observed bar spacing
  (15m bars annualise by `sqrt(35040)`, daily bars by `sqrt(365)`).
- **Accuracy.** Reported with a 95% confidence interval, a binomial p-value
  against the 50% null, and the majority-class baseline. A hit rate without
  those three numbers is not a result.
- **Baselines.** Every strategy figure is reported alongside buy-and-hold and a
  persistence (last-value) forecast over the identical window.
- **Validation.** Walk-forward with a purge and embargo of at least the longest
  feature lookback window between train and test folds.

### Sanity check

A null test ships in the repo: random entry signals on a driftless geometric
random walk, run through the engine with costs enabled. Because there is no
signal to find by construction, a correct engine must report a Sharpe at or
below zero. It reports **-5.13** over 75 days of synthetic 15m bars across three
symbols, and per-trade expectancy implied by the win rate and barrier levels
matches realised expectancy to within floating-point error — which is the
property the previous engine violated.

## Research Paper

The evaluation methodology, the defect taxonomy and the leakage case study are
written up in full as a manuscript in [`paper/`](paper/):

**[`crypto_direction_evaluation.tex`](paper/crypto_direction_evaluation.tex)** ---
*When the Feature Is the Label: Evaluation Defects and a Reproducible Baseline for
Daily Cryptocurrency Direction Forecasting.* Elsevier `elsarticle` format; compile
with pdfLaTeX three times (the bibliography is inline, so BibTeX is not needed).

Every figure in the paper is traceable to [`docs/evaluation_results.json`](docs/evaluation_results.json),
which is regenerated by `scripts/evaluate_walkforward.py`.

## Repository Map

| Path | What it holds |
|---|---|
| [`backend/backtest/engine.py`](backend/backtest/engine.py) | Corrected execution engine: bar-driven loop, per-asset pricing, next-bar fills, intrabar stops |
| [`backend/backtest/metrics.py`](backend/backtest/metrics.py) | Risk metrics: cadence-aware Sharpe, bootstrap Monte Carlo, drawdown-based Calmar |
| [`backend/ml/feature_engineer.py`](backend/ml/feature_engineer.py) | Feature pipeline with fit/transform separation and scaler persistence |
| [`backend/ml/model_trainer.py`](backend/ml/model_trainer.py) | Training pipeline, purge/embargo split, accuracy significance testing |
| [`scripts/evaluate_walkforward.py`](scripts/evaluate_walkforward.py) | Walk-forward evaluation harness producing every reported number |
| [`scripts/null_test_backtest.py`](scripts/null_test_backtest.py) | Falsification test: asserts zero edge on a random walk, non-zero exit on failure |
| [`tests/test_backtest_engine_correctness.py`](tests/test_backtest_engine_correctness.py) | 17 regression tests pinning the execution and metric defects |
| [`tests/test_feature_pipeline_correctness.py`](tests/test_feature_pipeline_correctness.py) | 9 regression tests pinning the scaler and split defects |
| [`paper/`](paper/) | Manuscript source |

## Mathematical Breakdown

| Component | Logic Applied | Working Model Concept |
|-----------|---------------|-----------------------|
| **Ensemble Logic** | Weighted average of $M$ model probabilities | **Macro-Signal Integration**: Synthesizes cross-paradigm forecasts into a unified consensus. |
| **Prophet** | Additive regression for trend/seasonality | **Structural Trend Decomposition**: Isolates long-term price trajectories from periodic cycles. |
| **LSTM** | Sequential memory gates (Forget/Input/Output) | **Chronological Memory Gates**: Processes non-linear time dependencies across historical OHLCV data. |
| **XGBoost** | Regularized Gradient Boosting (Newton-Raphson) | **High-Gain Residual Boosting**: Iteratively reduces model error by focusing on difficult-to-predict price splits. |
| **QLoRA** | Low-rank adapter updates to NF4 quantized weights | **Parameter-Efficient Adaptation**: Injects domain-specific sentiment intelligence into generalized LLMs. |
| **Evaluation** | Sharpe Ratio / Directional Accuracy Calculation | **Risk-Adjusted Alpha Scoring**: Statistically validates the probability of excess returns vs volatility. |

### 1. Unified Consensus (Ensemble)
The architecture achieves robustness by balancing three distinct forecasting methodologies. The final directional probability $P$ for a class $c$ is calculated by:
$$P_{\text{ensemble}}(c) = 0.3 \cdot P_{\text{prophet}}(c) + 0.4 \cdot P_{\text{lstm}}(c) + 0.3 \cdot P_{\text{xgb}}(c)$$

### 2. Market Cycle Analysis (Prophet)
Used to identify macro-trends by decomposing the signal into deterministic components:
$$y(t) = g(t) + s(t) + h(t) + \epsilon_t$$
- $g(t)$: Piecewise linear growth trend.
- $s(t)$: Fourier series for intraday/weekly periodicity.
- $h(t)$: Market holiday and anomalous event impacts.

### 3. Temporal Relationship Mapping (LSTM)
Utilizes a recursive neural architecture to protect long-term market context:
- **Forget Gate**: $f_t = \sigma(W_f \cdot [h_{t-1}, x_t] + b_f)$ (Controls information decay over time).
- **Input Gate**: $i_t = \sigma(W_i \cdot [h_{t-1}, x_t] + b_i)$ (Selects relevant new price features).
- **Cell State**: $C_t = f_t \odot C_{t-1} + i_t \odot \tanh(W_C \cdot [h_{t-1}, x_t] + b_C)$ (Stores the persistent market memory).

### 4. Optimized Decision Splits (XGBoost)
The model iteratively constructs shallow trees to minimize a regularized objective:
$$\mathcal{L}(\phi) = \sum_i l(\hat{y}_i, y_i) + \gamma T + \frac{1}{2}\lambda \sum w_j^2$$
This ensures the model generalizes well to unseen market data by penalizing excessive leaf nodes ($T$) and complex weights ($w$).

### 5. Efficient Knowledge Transfer (QLoRA)
Leverages the **Mistral-7B** foundational model for sentiment analysis using 4-bit precision compression:
$$W_{fixed} + \Delta W = W_{NF4} + (A \times B) \cdot \frac{\alpha}{r}$$
This concept allows the terminal to adapt massive transformer models to local crypto-sentiment tasks on standard consumer hardware.

### 6. Quantitative Validation Metrics

- **Directional Accuracy**: $Acc = \frac{1}{N} \sum \mathbb{1}(\text{sgn}(\Delta \hat{y}) = \text{sgn}(\Delta y))$, reported with its Wald interval $Acc \pm 1.96\sqrt{Acc(1-Acc)/N}$ and a two-sided test against the coin-flip null $z = (Acc - 0.5)/\sqrt{0.25/N}$. The point estimate alone is not interpretable.

- **Sharpe Ratio**: computed on equity-curve returns $r_t$ sampled at a fixed bar cadence, then annualised by the number of such bars per year:
$$S = \frac{\overline{r - r_f}}{\sigma_{r - r_f}} \sqrt{P}, \qquad P = \frac{\text{seconds per year}}{\text{seconds per bar}}$$
  $P$ is derived from the observed bar spacing, not assumed. Per-trade returns are **not** valid input here: trades do not arrive on a fixed clock, so no single $P$ exists for them.

- **Maximum Drawdown**: $MDD = \max_t \left( \frac{\max_{s \le t} V_s - V_t}{\max_{s \le t} V_s} \right)$ over the equity curve $V$, where a short contributes $-q \cdot p_t$ to $V_t$.

- **Confidence Layer**: $\text{Conf} = \left( \frac{1}{M} \sum \max(P_m) \right) \times \text{Multiplier}$ (Measures divergence between independent models).



### Model made at Haackathon in NMIMS, Indore
- Time taken to build this model = 18hrs
