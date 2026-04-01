import time

from backend.signal.generator import generate_signal


def run() -> None:
    while True:
        output = generate_signal("BTCUSDT")
        print(f"signal_generator output={output}")
        time.sleep(10)


if __name__ == "__main__":
    run()
