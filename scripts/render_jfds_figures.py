"""Generate vector research figures and verified mutation table from saved results."""
import json
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def main():
    result = json.loads((ROOT / "docs/jfds/results.json").read_text())
    folder = ROOT / "paper/jfds_overleaf/figures"
    folder.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.family":"DejaVu Sans", "font.size":9, "axes.spines.top":False,
                         "axes.spines.right":False, "pdf.fonttype":42})
    fig, (a,b) = plt.subplots(1,2,figsize=(10,3.7), constrained_layout=True)
    pairs = [r for r in result["paired_hac"] if r["model"] == "logistic"]
    means = np.array([100*r["diff"] for r in pairs])
    ci = np.array([r["ci95"] for r in pairs])*100
    a.errorbar(means, np.arange(3), xerr=np.array([means-ci[:,0], ci[:,1]-means]),
               fmt="o", color="#175678", capsize=4)
    a.set_yticks(np.arange(3), [r["symbol"].replace("USDT", "") for r in pairs])
    a.invert_yaxis()
    a.axvline(0, color=".5", ls="--", lw=1)
    a.set_xlabel("Accuracy difference (percentage points)")
    a.set_title("(a) Logistic minus inverse persistence\n95% HAC intervals, 20 lags", loc="left")
    c = result["cost_curve"]
    b.plot([r["bps"] for r in c], [r["sharpe"] for r in c], color="#175678", marker="o", ms=3)
    b.axhline(0, color=".5", lw=1)
    b.axvline(9, color="#a45625", ls="--", lw=1, label="Assumed cost: 9 bps")
    b.set_xlabel("Cost per unit of one-way turnover (bps)")
    b.set_ylabel("Annualized Sharpe ratio")
    b.set_title("(b) Target-notional cost sensitivity\nPoint estimates, not execution guarantees", loc="left")
    b.legend(frameon=False, fontsize=8)
    for ax in (a,b):
        ax.grid(axis="x", alpha=.15)
    fig.savefig(folder / "evidence.pdf", metadata={"Creator":"Matplotlib", "CreationDate":None, "ModDate":None})
    fig.savefig(folder / "evidence.png", dpi=400)
    plt.close(fig)
    mutations = json.loads((ROOT / "docs/jfds/mutation_matrix.json").read_text())
    names = {"D2_lookahead_price_lookup":"Future-bar price lookup", "D4_short_mark_to_market":"Wrong short valuation sign",
             "D5_close_only_exits":"Close-only stop checks", "D6_sharpe_annualisation":"Incorrect Sharpe annualization",
             "D7_monte_carlo_permutation":"Permutation used as resampling", "D10_scaler_refit":"Refit scaler during inference",
             "D11_no_embargo":"Remove specified split gap", "DX_fee_split_evenly":"Split fees equally across legs"}
    lines = [f"{names[r['mutation']]} & {r['n_failed']} & {'Detected' if r['killed'] else 'Survived'} \\\\" for r in mutations["results"]]
    (ROOT / "paper/jfds_overleaf/generated/mutations.tex").write_text("\n".join(lines)+"\n", encoding="utf-8")
    print("Wrote evidence.pdf, evidence.png and mutations.tex")


if __name__ == "__main__":
    main()
