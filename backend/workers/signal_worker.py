import time

from backend.signal.generator import generate_signal


def run() -> None:
    symbols = ["BTCUSDT", "ETHUSDT", "DOGEUSDT"]
    while True:
        for symbol in symbols:
            signal = generate_signal(symbol)
            print(f"signal_worker signal={signal}")
        time.sleep(10)


if __name__ == "__main__":
    run()
