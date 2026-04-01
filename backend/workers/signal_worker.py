import time

from backend.signal.generator import generate_signal


def run() -> None:
    while True:
        signal = generate_signal("BTCUSDT")
        print(f"signal_worker signal={signal}")
        time.sleep(10)


if __name__ == "__main__":
    run()
