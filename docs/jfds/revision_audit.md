# Audit trail for the updated manuscript

The new manuscript is `paper/jfds_overleaf/main.tex`. Original attachments and earlier manuscript/result files are preserved. This is a new measured revision, not a claim that the historical code causing every old reported number has been reproduced.

| Supplied claim or convention | Correction in this revision |
|---|---|
| 34 technical features | Executable inventory is 30; test asserts count and causal prefix invariance. |
| Pooled Bernoulli significance | Pooled accuracy is descriptive; primary pooled difference uses synchronized dates and missing masks, preserving cross-asset dependence. |
| Paired iid/weak HAC reporting | Explicit 20-lag Bartlett HAC, hand-computed fixture, confidence intervals, and validation of degenerate/invalid samples. |
| Break-even by interpolating Sharpe | Exact mean-gross/mean-turnover ratio; both terms resampled jointly. Negative bootstrap roots are retained. |
| No terminal portfolio action | Target-turnover diagnostic includes final liquidation; its approximate notional convention is explicit. |
| BTC/ETH weighted 1/3 before DOGE eligibility | Passive basket uses the same active asset set and equal weights as strategy. It is called a rebalanced basket, not buy-and-hold. |
| Strategy called market neutral | Signed unit-gross positions can have directional net exposure; no neutrality claim. |
| Matching accuracy implies no value | Separate paired net-return comparison included. It remains uncertain; positive common-window and long/cash results are not hidden. |
| Engine versus vector series treated as causal timing decomposition | Separate, fully specified daily-open-to-close cash ledger with exact own-notional fees; different policy is not a one-factor attribution experiment. |
| Overlapping two-day returns treated as daily tradable strategy | Removed from reported empirical evidence. |
| Years of pre-OOS cash/risk-free adjustment in reported engine Sharpe | New ledger uses only the 2,083 OOS earning days, explicit initial capital, zero risk-free rate, and daily flat positions. |
| Sentiment score described as independent news | Original code explicitly generates a price-based simulation. Exact outer clipping and rounding are described; all sentiment inputs excluded. |
| Contemporaneous correlation proves all future uses are leakage | Availability and label horizon distinguished. A known-at-close proxy can be causal for a later label, though not an independent text source. |
| A 2,400-bar, 15-minute fixture described as 75 days | It is 25 simultaneous-calendar days per path. Old null claims are not carried into the new paper. |
| Zero Gaussian log drift called zero expected simple return | These differ after exponentiation. No universal calibration claim based on the old generator. |
| Eight mutants said to prove all 12/13 faults fixed | Eight specified selected mutants only. Test runner rejects collection errors, missing/skipped tests, and unexpected detection behavior. |
| Shared causal past windows automatically called leakage | Label-information separation is checked; sharing past history alone is not treated as evidence of leakage. |
| Nonsignificance treated as equivalence or absence of alpha | No equivalence/absence claim. Intervals and conditional scope are explicit. |
| Hyperparameters described as preregistered | Fixed inherited source settings; retrospective analysis with no documented preregistration or untouched final holdout. |
| Future archive and author review asserted as completed | Local archives are actually generated; public deposit, human review, author declarations and permissions remain unresolved. |

## What was actually built and measured

- 72 saved fold-specific pipelines (logistic, boosted trees and lag-1 logistic).
- 5,562 prediction records with dates, labels, probabilities and baselines.
- HAC accuracy comparisons; date-synchronized bootstrap CIs at block lengths 5/20/60.
- Joint return-turnover cost-root inference, economic baseline comparisons, common-window/long-cash/gating sensitivities.
- Explicit flat-at-daily-close trade and equity ledgers with reconciliation checks.
- 51 focused tests and eight detected selected mutants (consult regenerated logs).
- Input hashes; 275 asset-days matched against nine checksum-verified Binance monthly archives.
- Automatically generated LaTeX numbers, tables and analytical figure.

Not completed or claimed: a new state-of-the-art model architecture; genuine historical sentiment collection; full live platform validation; prospective/paper trading; order-book execution or funding/borrow estimation; untouched final holdout; public deposit or journal submission.
