import time

from backend.backtest.engine import run_backtest


def run() -> None:
    while True:
        report = run_backtest("baseline")
        print(f"backtest_worker report={report}")
        time.sleep(30)


if __name__ == "__main__":
    run()
