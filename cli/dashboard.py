from rich.console import Console
from rich.table import Table


def render_dashboard() -> None:
    console = Console()
    table = Table(title="BINFIN Terminal")
    table.add_column("Symbol")
    table.add_column("Signal")
    table.add_column("Confidence")
    table.add_row("BTCUSDT", "BUY", "0.80")
    console.print(table)
