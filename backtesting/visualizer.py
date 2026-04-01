from __future__ import annotations

import matplotlib.pyplot as plt
import pandas as pd


def plot_equity_curve(equity_curve: pd.Series, output_path: str | None = None) -> None:
    plt.figure(figsize=(10, 4))
    plt.plot(equity_curve.index, equity_curve.values, label="Equity")
    plt.title("Backtest Equity Curve")
    plt.xlabel("Time")
    plt.ylabel("Equity")
    plt.legend()
    plt.tight_layout()
    if output_path:
        plt.savefig(output_path)
    else:
        plt.show()
