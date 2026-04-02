import typer
import asyncio
import os

from cli.dashboard import render_dashboard
from cli.terminal import TerminalDashboard
from backend.ml.model_trainer import ModelTrainer

app = typer.Typer(help="BINFIN CLI")


@app.command()
def dashboard() -> None:
    render_dashboard()


@app.command()
def terminal() -> None:
    TerminalDashboard().run()


@app.command()
def train(
    coin: str = typer.Option("BTC", help="Coin symbol to train, or ALL for all tracked coins."),
    lookback_days: int = typer.Option(90, help="Lookback window in days for training."),
) -> None:
    trainer = ModelTrainer()

    async def _run() -> None:
        if coin.strip().upper() == "ALL":
            tracked = [
                item.strip().upper()
                for item in os.getenv("TRACKED_COINS", "BTC,ETH,DOGE").split(",")
                if item.strip()
            ]
            for item in tracked:
                result = await trainer.train_pipeline(coin=item, lookback_days=lookback_days)
                print(f"[TRAINED] {item}: {result.get('status')}")
        else:
            result = await trainer.train_pipeline(coin=coin.strip().upper(), lookback_days=lookback_days)
            print(result)

    asyncio.run(_run())


if __name__ == "__main__":
    app()
