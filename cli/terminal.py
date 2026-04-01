from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from typing import Any

import httpx
from rich import box
from rich.align import Align
from rich.console import Console, Group, RenderableType
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.text import Text


class TerminalDashboard:
    def __init__(self) -> None:
        self.console = Console()
        self.base_url = os.getenv("BACKEND_URL", "http://localhost:8000")
        self.refresh_seconds = float(os.getenv("TERMINAL_REFRESH_SECONDS", "2"))
        self.coins = [c.strip().upper() for c in os.getenv("TRACKED_COINS", "BTC,ETH,SOL,ADA,DOT").split(",") if c.strip()]
        self.layout = self.create_layout()
        self.recent_logs: list[dict[str, str]] = []
        self._quit = False
        self._last_payload: dict[str, Any] = {}

    def create_layout(self) -> Layout:
        layout = Layout(name="root")
        layout.split(
            Layout(name="header", size=5),
            Layout(name="body", ratio=1),
            Layout(name="footer", size=12),
        )
        layout["body"].split_row(
            Layout(name="left", ratio=3),
            Layout(name="center", ratio=4),
            Layout(name="right", ratio=4),
        )
        layout["footer"].split_row(
            Layout(name="metrics", ratio=3),
            Layout(name="logs", ratio=7),
        )
        return layout

    def render_header(self, connected: bool) -> Panel:
        title = Text("BINFIN TERMINAL", style="bold cyan")
        subtitle = Text("Crypto Trading Intelligence", style="white")
        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        status = Text("CONNECTED", style="bold green") if connected else Text("DISCONNECTED", style="bold red")

        group = Group(
            Align.center(title),
            Align.center(subtitle),
            Align.center(Text(f"Time: {now}   Status: ") + status),
        )
        return Panel(group, border_style="cyan", box=box.ROUNDED)

    def render_price_tickers(self, coins: list[str], payload: dict[str, Any]) -> Table:
        table = Table(title="Price Tickers", box=box.SIMPLE_HEAVY)
        table.add_column("Symbol", style="bold")
        table.add_column("Price", justify="right")
        table.add_column("24h %", justify="right")
        table.add_column("Volume", justify="right")
        table.add_column("Spark", justify="left")

        for coin in coins:
            details = payload.get("coins", {}).get(coin, {}).get("details", {})
            history = payload.get("coins", {}).get(coin, {}).get("price_history", [])
            price = float(details.get("current_price") or 0.0) if isinstance(details, dict) else 0.0
            change = float(details.get("change_24h_pct") or 0.0) if isinstance(details, dict) else 0.0
            volume = 0.0
            if history and isinstance(history, list):
                latest = history[0]
                volume = float(latest.get("volume") or 0.0)

            trend = self._sparkline([float(item.get("close") or 0.0) for item in history[:24]])
            change_style = "green" if change >= 0 else "red"
            table.add_row(
                coin,
                f"{price:,.4f}",
                f"[{change_style}]{change:+.2f}%[/{change_style}]",
                f"{volume:,.2f}",
                trend,
            )
        return table

    def render_sentiment_panel(self, coins: list[str], payload: dict[str, Any]) -> Panel:
        sentiment_rows = payload.get("sentiment", []) if isinstance(payload.get("sentiment"), list) else []
        score_by_coin: dict[str, list[float]] = {coin: [] for coin in coins}
        for row in sentiment_rows:
            symbol = str(row.get("symbol", "")).upper()
            score = float(row.get("sentiment_score") or 0.5)
            if symbol in score_by_coin:
                score_by_coin[symbol].append(score)

        lines: list[Text] = []
        for coin in coins:
            scores = score_by_coin.get(coin, [])
            avg = sum(scores) / len(scores) if scores else 0.5
            if avg > 0.66:
                icon = "G"
                color = "green"
                arrow = "^"
            elif avg < 0.34:
                icon = "R"
                color = "red"
                arrow = "v"
            else:
                icon = "Y"
                color = "yellow"
                arrow = ">"
            bar = self._score_bar(avg, width=20)
            lines.append(Text.from_markup(f"[{color}]{coin} {icon} {bar} {avg:.2f} {arrow}[/{color}]"))

        return Panel(Group(*lines) if lines else Text("No sentiment data"), title="Sentiment", border_style="magenta")

    def render_signals_list(self, signals: list[dict[str, Any]]) -> Table:
        table = Table(title="Signals", box=box.SIMPLE)
        table.add_column("Time")
        table.add_column("Coin")
        table.add_column("Type")
        table.add_column("Strength", justify="right")
        table.add_column("Confidence", justify="right")
        table.add_column("Why")

        for item in signals[:12]:
            ts = str(item.get("ts", ""))[:19]
            symbol = str(item.get("symbol", ""))
            sig = str(item.get("signal", "HOLD")).upper()
            style = "green" if sig == "BUY" else "red" if sig == "SELL" else "yellow"
            rationale = str(item.get("rationale") or "")[:45]
            table.add_row(
                ts,
                symbol,
                f"[{style}]{sig}[/{style}]",
                f"{float(item.get('strength') or 0.0):.2f}",
                f"{float(item.get('confidence') or 0.0):.2f}",
                rationale,
            )
        return table

    def render_system_metrics(self, payload: dict[str, Any]) -> Panel:
        checks = payload.get("health", {}).get("checks", {}) if isinstance(payload.get("health"), dict) else {}
        cpu = self._extract_metric(payload, "cpu_percent")
        ram = self._extract_metric(payload, "memory_percent")
        disk = checks.get("disk", {}).get("percent", 0) if isinstance(checks.get("disk"), dict) else 0

        queue_sent = len(payload.get("sentiment", [])) if isinstance(payload.get("sentiment"), list) else 0
        queue_pred = len(payload.get("predictions", [])) if isinstance(payload.get("predictions"), list) else 0

        lines = [
            Text(f"CPU  {self._score_bar(cpu / 100, 24)} {cpu:.1f}%", style="cyan"),
            Text(f"RAM  {self._score_bar(ram / 100, 24)} {ram:.1f}%", style="cyan"),
            Text(f"Disk {self._score_bar(float(disk) / 100, 24)} {float(disk):.1f}%", style="cyan"),
            Text(f"Queues sentiment={queue_sent} prediction={queue_pred}", style="white"),
        ]
        return Panel(Group(*lines), title="System Metrics", border_style="blue")

    def render_logs(self, recent_logs: list[dict[str, str]]) -> Panel:
        if not recent_logs:
            return Panel("No logs yet", title="Logs")

        lines: list[Text] = []
        for row in recent_logs[-5:]:
            level = row.get("level", "INFO").upper()
            style = "green" if level == "INFO" else "yellow" if level == "WARNING" else "red"
            lines.append(Text.from_markup(f"[{style}]{row.get('time', '')} {level:<7} {row.get('message', '')}[/{style}]"))
        return Panel(Group(*lines), title="Logs", border_style="white")

    async def update_loop(self, live: Live) -> None:
        while not self._quit:
            try:
                payload = await self._fetch_payload()
                self._last_payload = payload
                connected = not bool(payload.get("error"))

                self.layout["header"].update(self.render_header(connected))
                self.layout["left"].update(self.render_price_tickers(self.coins, payload))
                self.layout["center"].update(self.render_sentiment_panel(self.coins, payload))
                self.layout["right"].update(self.render_signals_list(payload.get("active_signals", [])))
                self.layout["metrics"].update(self.render_system_metrics(payload))
                self.layout["logs"].update(self.render_logs(self.recent_logs))
                live.update(self.layout)

                self._handle_keyboard()
                await asyncio.sleep(self.refresh_seconds)
            except KeyboardInterrupt:
                self._quit = True
            except Exception as exc:
                self._log("ERROR", f"update loop failed: {exc}")
                await asyncio.sleep(self.refresh_seconds)

    def run(self) -> None:
        self._log("INFO", "Starting terminal dashboard")
        with Live(self.layout, console=self.console, refresh_per_second=4, screen=True) as live:
            try:
                asyncio.run(self.update_loop(live))
            except KeyboardInterrupt:
                self._quit = True
            finally:
                self._log("INFO", "Terminal dashboard stopped")

    async def _fetch_payload(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "health": {},
            "active_signals": [],
            "sentiment": [],
            "predictions": [],
            "coins": {},
        }
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                health = await client.get(f"{self.base_url}/api/v1/health/")
                result["health"] = health.json()

                signals = await client.get(f"{self.base_url}/api/v1/signals/active", params={"limit": 100})
                result["active_signals"] = signals.json()

                sentiment = await client.get(f"{self.base_url}/api/v1/sentiment/", params={"hours": 48, "limit": 200})
                result["sentiment"] = sentiment.json()

                predictions = await client.get(f"{self.base_url}/api/v1/predictions/", params={"hours": 24, "limit": 200})
                result["predictions"] = predictions.json()

                for coin in self.coins:
                    details = await client.get(f"{self.base_url}/api/v1/coins/{coin}")
                    hist = await client.get(
                        f"{self.base_url}/api/v1/coins/{coin}/price-history",
                        params={"interval": "15m", "limit": 60},
                    )
                    result["coins"][coin] = {
                        "details": details.json(),
                        "price_history": hist.json(),
                    }
        except Exception as exc:
            result["error"] = str(exc)
            self._log("ERROR", f"API connection failed: {exc}")
        return result

    def _handle_keyboard(self) -> None:
        try:
            import msvcrt

            if not msvcrt.kbhit():
                return
            char = msvcrt.getch().decode("utf-8", errors="ignore").lower()
            if char == "q":
                self._log("INFO", "Quit command received")
                self._quit = True
            elif char == "r":
                self._log("INFO", "Manual refresh command")
            elif char == "s":
                self._show_signal_details()
            elif char == "b":
                self._run_backtest_command()
            elif char == "h":
                self._show_help()
        except Exception:
            return

    def _show_signal_details(self) -> None:
        signals = self._last_payload.get("active_signals", []) if isinstance(self._last_payload, dict) else []
        if not signals:
            self._log("WARNING", "No active signals for details")
            return
        item = signals[0]
        self._log("INFO", f"Signal detail: {item.get('symbol')} {item.get('signal')} conf={item.get('confidence')}")

    def _run_backtest_command(self) -> None:
        self._log("INFO", "Backtest command requested (use web dashboard for full config)")

    def _show_help(self) -> None:
        self._log("INFO", "Keyboard shortcuts: q=quit s=signal detail b=backtest r=refresh h=help")

    def _log(self, level: str, message: str) -> None:
        self.recent_logs.append(
            {
                "time": datetime.now(UTC).strftime("%H:%M:%S"),
                "level": level,
                "message": message,
            }
        )
        self.recent_logs = self.recent_logs[-50:]

    def _sparkline(self, values: list[float]) -> str:
        if not values:
            return ""
        blocks = "._-~=*#"
        lo = min(values)
        hi = max(values)
        spread = hi - lo if hi != lo else 1.0
        out = []
        for value in values[-18:]:
            idx = int((value - lo) / spread * (len(blocks) - 1))
            out.append(blocks[idx])
        return "".join(out)

    def _score_bar(self, score: float, width: int = 20) -> str:
        score = max(0.0, min(1.0, score))
        filled = int(round(score * width))
        return "[" + ("#" * filled) + ("-" * (width - filled)) + "]"

    def _extract_metric(self, payload: dict[str, Any], key: str) -> float:
        checks = payload.get("health", {}).get("checks", {}) if isinstance(payload.get("health"), dict) else {}
        memory = checks.get("memory", {}) if isinstance(checks.get("memory"), dict) else {}
        if key == "memory_percent":
            return float(memory.get("percent") or 0.0)
        if key == "cpu_percent":
            return 0.0
        return 0.0


def main() -> None:
    dashboard = TerminalDashboard()
    dashboard.run()


if __name__ == "__main__":
    main()
