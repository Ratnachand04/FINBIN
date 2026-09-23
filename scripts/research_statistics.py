"""Explicit statistical and execution contracts used by the JFDS manuscript."""
from __future__ import annotations

import math
import numpy as np


def paired_hac(a, b, lags=20):
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    if a.ndim != 1 or a.shape != b.shape or a.size < 2:
        raise ValueError("Paired samples must be equal-length vectors with n >= 2")
    if not np.isfinite(a).all() or not np.isfinite(b).all():
        raise ValueError("Non-finite observations are not allowed")
    if isinstance(lags, bool) or not isinstance(lags, (int, np.integer)) or lags < 0:
        raise ValueError("lags must be a nonnegative integer")
    d = a - b
    mean, n = float(d.mean()), len(d)
    x = d - mean
    k = min(lags, n - 1)
    variance = float(x @ x / n)
    for j in range(1, k + 1):
        variance += 2 * (1 - j / (k + 1)) * float(x[j:] @ x[:-j] / n)
    se = math.sqrt(max(0., variance) / n)
    if se == 0:
        z, p = (0., 1.) if mean == 0 else (None, None)
    else:
        z = mean / se
        p = math.erfc(abs(z) / math.sqrt(2))
    return dict(diff=mean, se_hac=se, z_hac=z, p_value=p, hac_lags=k,
                ci95=[mean - 1.95996398454 * se, mean + 1.95996398454 * se],
                status="degenerate_nonzero" if se == 0 and mean != 0 else "ok")


def sharpe(r, periods=365):
    r = np.asarray(r, dtype=float)
    if r.ndim != 1 or len(r) < 2 or not np.isfinite(r).all():
        raise ValueError("Sharpe requires at least two finite periodic returns")
    sd = r.std(ddof=1)
    return float(r.mean() / sd * np.sqrt(periods)) if sd > 0 else None


def stationary_indices(n, replicates=2000, mean_block=20, seed=20260401):
    """Circular stationary bootstrap: identical indices for every dated column."""
    if n < 2 or replicates < 2 or mean_block < 1:
        raise ValueError("Invalid bootstrap dimensions or mean block length")
    rng = np.random.default_rng(seed)
    ix = np.empty((replicates, n), dtype=np.int32)
    ix[:, 0] = rng.integers(n, size=replicates)
    for t in range(1, n):
        restart = rng.random(replicates) < 1 / mean_block
        fresh = rng.integers(n, size=replicates)
        ix[:, t] = np.where(restart, fresh, (ix[:, t - 1] + 1) % n)
    return ix


def percentile(x):
    x = np.asarray(x, dtype=float)
    if not np.isfinite(x).all():
        raise ValueError("Undefined bootstrap statistic: do not silently discard replicates")
    return [float(v) for v in np.quantile(x, [.025, .975])]


def portfolio_series(pred, returns, long_only=False, gate=None, proba=None):
    """Linear target-notional diagnostic, not a drift-adjusted trading account.

    Includes initial entry and final target liquidation in turnover. Missing
    assets receive zero weight; active assets receive 1 / active count.
    """
    pred, returns = np.asarray(pred, float), np.asarray(returns, float)
    if pred.shape != returns.shape or returns.ndim != 2:
        raise ValueError("Prediction and return panels must share date/asset axes")
    present = np.isfinite(returns)
    if not present.any(axis=1).all() or not np.isfinite(pred[present]).all():
        raise ValueError("Missing prediction or empty date")
    sign = np.where(pred == 1, 1., 0. if long_only else -1.)
    if gate is not None:
        sign *= np.abs(np.asarray(proba, float) - .5) >= gate
    weights = np.where(present, sign, 0.) / present.sum(axis=1, keepdims=True)
    gross = (weights * np.nan_to_num(returns)).sum(axis=1)
    turn = np.abs(np.diff(weights, axis=0, prepend=np.zeros((1, weights.shape[1])))).sum(axis=1)
    turn[-1] += np.abs(weights[-1]).sum()
    passive = np.nanmean(returns, axis=1)
    return gross, turn, passive


def cost_statistics(gross, turn, indices, cost_bps=9):
    gross, turn = np.asarray(gross, float), np.asarray(turn, float)
    if gross.shape != turn.shape or np.any(turn < 0) or turn.mean() <= 0:
        raise ValueError("Positive aggregate turnover is required for a cost root")
    net = gross - cost_bps / 10000 * turn
    bg, bt = gross[indices], turn[indices]
    bn = bg - cost_bps / 10000 * bt
    root = 10000 * gross.mean() / turn.mean()
    boot_root = 10000 * bg.mean(axis=1) / bt.mean(axis=1)
    bs_g = np.sqrt(365) * bg.mean(axis=1) / bg.std(axis=1, ddof=1)
    bs_n = np.sqrt(365) * bn.mean(axis=1) / bn.std(axis=1, ddof=1)
    return dict(days=len(gross), gross_sharpe=sharpe(gross), net_sharpe=sharpe(net),
                gross_ci95=percentile(bs_g), net_ci95=percentile(bs_n),
                breakeven_bps=float(root), breakeven_ci95=percentile(boot_root),
                mean_daily_gross=float(gross.mean()), mean_daily_net=float(net.mean()),
                annual_net_mean=float(365 * net.mean()),
                annual_net_vol=float(np.sqrt(365) * net.std(ddof=1)),
                turnover=float(turn.mean()), daily_cost_bps=float(cost_bps * turn.mean()),
                cost_bps=cost_bps)


def round_trip_return(side, open_price, close_price, fee=.0004, slip=.0005):
    """One bar; signed cash flows, own-notional entry/exit fees, no barriers."""
    if side not in (-1, 1) or open_price <= 0 or close_price <= 0:
        raise ValueError("Invalid side or price")
    if not 0 <= fee < 1 or not 0 <= slip < 1:
        raise ValueError("Invalid friction parameter")
    entry = open_price * (1 + side * slip)
    exit_ = close_price * (1 - side * slip)
    return side * (exit_ / entry - 1) - fee * (1 + exit_ / entry)
