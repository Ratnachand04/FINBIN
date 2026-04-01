import time

from backend.collectors.market_collector import collect_market_tick


def run() -> None:
    while True:
        tick = collect_market_tick("BTCUSDT")
        print(f"collector_worker tick={tick}")
        time.sleep(10)


if __name__ == "__main__":
    run()
