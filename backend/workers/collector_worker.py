import time

from backend.collectors.market_collector import collect_market_tick


def run() -> None:
    symbols = ["BTCUSDT", "ETHUSDT", "DOGEUSDT"]
    while True:
        for symbol in symbols:
            tick = collect_market_tick(symbol)
            print(f"collector_worker tick={tick}")
        time.sleep(10)


if __name__ == "__main__":
    run()
