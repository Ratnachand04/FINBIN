"""Read-only spot check against Binance's public, checksum-published archive.

Does not replace the supplied data. Network access is optional for reproduction.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, UTC
import hashlib
import io
import json
from pathlib import Path
import urllib.request
import zipfile

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
FIELDS = {1:"open", 2:"high", 3:"low", 4:"close", 5:"volume", 7:"quote_asset_volume",
          8:"number_of_trades", 9:"taker_buy_base_asset_volume"}


def verify(task):
    symbol, month = task
    url = f"https://data.binance.vision/data/spot/monthly/klines/{symbol}/1d/{symbol}-1d-{month}.zip"
    raw = urllib.request.urlopen(url, timeout=30).read()
    checksum = urllib.request.urlopen(url + ".CHECKSUM", timeout=30).read().decode().split()[0]
    digest = hashlib.sha256(raw).hexdigest()
    if checksum != digest:
        raise ValueError(f"Provider archive checksum mismatch: {url}")
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        official = pd.read_csv(z.open(z.namelist()[0]), header=None)
    official["date"] = pd.to_datetime(official[0], unit="us" if month >= "2025-01" else "ms")
    local = pd.read_csv(ROOT / f"data_ingestion/output/klines/{symbol}_daily.csv", parse_dates=["date"])
    merged = local.merge(official, on="date", how="inner", validate="one_to_one")
    counts = {name: int(np.isclose(merged[name], merged[col], rtol=1e-10, atol=1e-8).sum())
              for col, name in FIELDS.items()}
    return dict(symbol=symbol, month=month, url=url, archive_sha256=digest,
                checksum_verified=True, compared_rows=len(merged),
                matching_rows_by_field=counts, all_compared_fields_match=all(v == len(merged) for v in counts.values()))


def main():
    tasks = [(s,m) for s in ("BTCUSDT", "ETHUSDT", "DOGEUSDT")
             for m in ("2019-07", "2023-01", "2026-03")]
    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(verify, tasks))
    result = dict(checked_utc=datetime.now(UTC).isoformat(),
                  purpose="Selected-month provenance check, not a full-history independent validation",
                  relative_tolerance=1e-10, absolute_tolerance=1e-8,
                  compared_rows=sum(r["compared_rows"] for r in records),
                  all_match=all(r["all_compared_fields_match"] for r in records), records=records)
    out = ROOT / "docs/jfds/source_spot_check.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["all_match"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
