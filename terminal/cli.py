from __future__ import annotations

from datetime import UTC, datetime

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="BINFIN terminal dashboard")
console = Console()


@app.command()
def status() -> None:
    table = Table(title=f"BINFIN Status {datetime.now(UTC).isoformat()}")
    table.add_column("Symbol")
    table.add_column("Price", justify="right")
    table.add_column("Sentiment", justify="right")
    table.add_column("Signal")

    rows = [
        ("BTCUSDT", "70,000", "0.62", "BUY"),
        ("ETHUSDT", "3,500", "0.57", "BUY"),
        ("DOGEUSDT", "0.25", "0.54", "HOLD"),
    ]
    for row in rows:
        table.add_row(*row)

    console.print(table)


if __name__ == "__main__":
    app()
