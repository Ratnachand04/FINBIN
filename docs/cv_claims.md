# CV claims — exact figures and what is / is not defensible

Every number below regenerates from the repo. Sources: `docs/evaluation_results.json`,
`docs/ablation_results.json`, `docs/decision_layer.json`, `docs/api_latency.json`,
`docs/mutation_matrix.json`.

Repo: **github.com/Ratnachand04/FINBIN** — commit `83a31ac` for the evaluation.

---

## 1. FIX FIRST: the URL on your CV is wrong

Your CV reads `github.com/Ratnachand04/Crypto-Intelligence-Terminal`.
The repository is `github.com/Ratnachand04/FINBIN`. The link as printed **404s**.
Either rename the repo on GitHub or fix the CV. A dead link on a quant CV is fatal
before anyone reads a word.

---

## 2. CANNOT be claimed — remove these

| Placeholder on your CV | Why it cannot be filled |
|---|---|
| `[N] labelled posts` | There are none. The 8,760-row SFT file is generated from a synthetic sentiment series, not labelled human posts. |
| `[macro-F1]` for QLoRA | Never measured. No labelled evaluation set exists. |
| `FinBERT baseline of [Y]` | No such comparison was ever run. |
| `net of taker fees` on directional accuracy | Category error — accuracy has no fees. Fees apply to P&L, not hit rate. |
| `[Y]% persistence baseline` | Technically 47.1%, but quoting it is **misleading**: its inverse (52.88%) is the real baseline and it ties your model. See §4. |

**The entire QLoRA bullet must be rewritten as engineering, not as a metric.**
The fine-tune ran; its quality was never evaluated. Claiming an F1 you did not
measure is the single highest-risk thing on this CV.

---

## 3. Verified figures (all defensible)

### Research
| Quantity | Value |
|---|---|
| Out-of-sample predictions | **5,562** (BTC 2,083 / ETH 2,083 / DOGE 1,396) |
| Period | 2020-07-17 → 2026-03-30, daily bars, Binance spot |
| Protocol | Expanding walk-forward, 1,000-day initial train, 250-day folds, **5-day embargo**, per-fold scaler fit |
| Directional accuracy | **52.95%**, Wilson 95% CI [51.6, 54.3], **p < 1e-4** |
| Pesaran–Timmermann | p = 0.015 / 0.008 / 0.016 (BTC/ETH/DOGE) |
| Inverse-persistence baseline | **52.88%** — paired Δ = −0.29 / +0.53 / −0.07 pp, \|t\| ≤ 0.45 |
| Gross annualised Sharpe | **0.544**, stationary-bootstrap 95% CI **[−0.17, +1.28]** |
| Net Sharpe @ 9 bp/side | 0.113 |
| Breakeven cost | **11.4 bp/side**, 95% CI [0, 26.9] |
| Appraisal ratio vs passive | **−0.076** (corr +0.224, beta +0.215) |
| Return-weighted hit rate | 51.68 / 51.57 / 52.33% — *below* unweighted |

### Ablation
| Quantity | Value |
|---|---|
| Ensemble vs best single member | Beats it on **zero** of 3 assets (−0.24 / −0.14 / −1.36 pp) |
| LSTM accuracy | 50.29 / 50.62 / **48.89%** — worst member everywhere |
| Deployed weights | 0.30 / **0.40** / 0.30 (stat / neural / boosted) |
| Fitted weights | 0.59/0.46/0.55 · **0.24/0.24/0.24** · 0.16/0.30/0.21 |
| LSTM inference cost | **920×** logistic (49.685 ms vs 0.054 ms p50) |

### Decision layer
| Quantity | Value |
|---|---|
| Signals fired | **0** of 5,562 days, all assets |
| Confidence gate | 0.75 |
| Max P(up) ever produced | **0.798** (BTC) |
| Days clearing gate | 0.096% / 0.816% / 0.143% |

### Engineering
| Quantity | Value |
|---|---|
| Codebase | **17,200 LOC**, 169 modules, 45 endpoints, 8 containers |
| Backtest throughput | **293,479 bars/s** |
| Feature construction | **5.7 µs/bar** |
| Signals endpoint latency | **2.897 ms p50 / 6.141 ms p95**, 59 kB payload *(in-process ASGI, DB stubbed)* |
| Regression tests | 29, **mutation score 8/8** |
| Null test | 60 seeds; 13.3% of correct-engine seeds give positive Sharpe |
| Fault audit | 12 faults; reported Sharpe fell **4.54 → −0.35** |
| QLoRA config | Mistral-7B, NF4 double-quant, r=64, α=16, 7 target modules |
| Leakage found | Sentiment feature ρ = **0.916** same-day, **−0.018** next-day |

---

## 4. The trap you must not walk into

Persistence scores 47.1%. Quoting "52.95% vs 47.1% persistence baseline" is
technically true and reads as a **+5.9pp edge**.

Any quant at HRT or Citadel computes `100 − 47.1 = 52.9` in their head and asks:
*"so what does your model do that betting against yesterday doesn't?"*

The answer is **nothing** — paired Δ ≤ 0.53pp with |t| ≤ 0.45, and you lose on
2 of 3 assets. If you cite persistence and they catch it, you look either careless
or dishonest. If you cite inverse persistence yourself, you look like someone who
knows what a baseline is. **Cite the inverse.**

---

## 5. Recommended bullets

### Version A — Quant Research (HRT QR, Citadel QR/QD)

**Alternative-Data Signal Research & Evaluation Infrastructure** — Python, scikit-learn, TensorFlow, FastAPI · `github.com/Ratnachand04/FINBIN`

- Walk-forward evaluation (expanding window, 5-day purge/embargo, per-fold scaler fit) over **5,562 out-of-sample daily predictions** on BTC/ETH/DOGE, 2020–2026: **52.95% directional accuracy** (Wilson 95% CI [51.6, 54.3], p < 1e-4; Pesaran–Timmermann p < 0.02).
- Falsified my own result: paired tests on identical days show **no separation from inverse persistence** (52.88%; Δ = −0.29/+0.53/−0.07 pp, |t| ≤ 0.45) — 34 engineered features recover a known one-day reversal and nothing beyond it.
- Carried it to economics: gross Sharpe **0.544** (stationary-bootstrap 95% CI **[−0.17, +1.28]**, contains zero), breakeven cost **11.4 bp/side** (CI [0, 26.9]), appraisal ratio **−0.076** vs passive.
- Audited the backtester: found **12 faults** (look-ahead fills, cross-asset position pricing, √252 applied to per-trade returns, sign-inverted short MTM, permutation-invariant bootstrap) that had inflated reported Sharpe from **−0.35 to 4.54**; wrote 29 regression tests, a mutation rig (**8/8 kill**) and a randomised null test.

### Version B — Systems / Algo Development (HRT AlgoDev, Citadel SWE)

**Self-Hosted Alternative-Data Signal Engine** — Python, QLoRA, FastAPI, TimescaleDB, Docker · `github.com/Ratnachand04/FINBIN`

- **17.2k LOC across 8 containerised services**: Binance WS+REST / news / Reddit / on-chain ingestion → TimescaleDB hypertables → 4-bit NF4 QLoRA-adapted Mistral-7B (r=64) with FinBERT fallback → forecasting ensemble → FastAPI (**45 endpoints**, 3 WebSocket channels).
- Hybrid split runtime pins generation to GPU and retrieval to CPU with capability-based fallback to CPU LoRA; found PyTorch reports CUDA while TensorFlow reports none on the same host, so **capability detection must be per-framework, not per-host**.
- Measured: **293k bars/s** backtest, **5.7 µs/bar** feature construction, **2.9/6.1 ms p50/p95** signals endpoint (59 kB payload, in-process, DB stubbed).
- Ablation showed the 3-model ensemble **beats no single member**; fitting the weights cut the LSTM from 0.40 → 0.24 — a component costing **920× the inference latency** of the linear model that outperforms it.

### Version C — two lines, matching your current format

- Self-hosted Reddit/news/on-chain/price ingestion → TimescaleDB → 4-bit QLoRA Mistral-7B (r=64, NF4) + FinBERT fallback, served by FastAPI at **2.9/6.1 ms p50/p95** (59 kB payload, in-process); 17.2k LOC, 8 containers, 45 endpoints.
- Walk-forward over **5,562 OOS daily predictions** (BTC/ETH/DOGE, 2020–26, 5-day embargo): **52.95%** directional accuracy (p < 1e-4) but **no separation from inverse persistence** (52.88%, |t| ≤ 0.45); audit of the backtester found 12 faults that had inflated Sharpe **4.54 → −0.35**; mutation score **8/8**.

---

## 6. Interview defence

**"Your model doesn't beat a one-line rule. Why is this on your CV?"**
Because finding that out required building the thing that could find it out. The
same codebase reported Sharpe 4.54 before I wrote the harness. The deliverable is
a calibrated instrument; the negative result is evidence it works.

**"Walk me through the Sharpe annualisation bug."**
Sharpe was `mean/std × √252` on *per-trade* returns. Trades don't arrive on a
fixed clock, so no annualisation factor is defined for them. Fixed by computing
from the equity curve at bar cadence with the factor inferred from median bar
spacing — √35040 for 15m, √365 for daily. That one fault produced the 23.65
per-series maximum.

**"Why is the confidence gate at 0.75 a bug if the code is correct?"**
It's an interface defect, not a component defect. Both layers are individually
right. The gate was set without reference to the output distribution of the layer
feeding it — a calibrated model on a signal this weak tops out at 0.798, so the
rule fires zero times in 5,562 days. Component tests can't catch it; you need
assertions on the joint behaviour of adjacent layers.

**"Why does inverse persistence work?"**
Short-horizon reversal in daily crypto — documented for equities since
Lo & MacKinlay (1990) and Jegadeesh (1990). Persistence scores 47.1%, i.e.
significantly *worse* than chance, so its inverse is significantly better. Part
of what any model here learns is to invert momentum.

**"Your Sharpe CI contains zero. So you have nothing?"**
Correct, and that's the honest reading. Gross 0.544, CI [−0.17, +1.28] over
~5.7 years. Breakeven cost 11.4 bp/side with CI [0, 26.9], which contains my 9 bp
assumption. I can't claim tradability in either direction on this sample.

**"What would you do next?"**
Costs are the binding constraint (−0.431 Sharpe vs −0.001 for entry lag), so:
put costs in the loss function; add the crypto microstructure features the set
omits entirely — funding, open interest, basis, liquidations, order-book
imbalance, cross-asset lead-lag; and drop the LSTM, which is the worst member at
920× the cost.

---

## 7. Reality check on the process

This project is not what gets you an HRT or Citadel interview. Both gate on an
online assessment: probability, statistics, expected value, combinatorics,
algorithmic coding. Nobody reads a GitHub repo to decide whether to send an OA.

What this project does is survive the interview once you're in the room. Bullets
3–4 of Version A are the strongest lines here for a research seat, because
"I found twelve faults in my own measurement code and reported the corrected
numbers" is a rarer signal than any accuracy figure.

Allocation: **~70% OA preparation, ~30% this project.**
