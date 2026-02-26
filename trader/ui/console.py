"""Terminal output helpers."""

from __future__ import annotations

from dataclasses import dataclass

from rich.console import Console


@dataclass
class BotConsole:
    console: Console

    @classmethod
    def create(cls) -> "BotConsole":
        return cls(console=Console())

    def header(self, text: str) -> None:
        self.console.print(f"[cyan]{text}[/cyan]")

    def decision(self, action: str, confidence: float, edge: float, reason: str, remaining_sec: int) -> None:
        color = "green" if action != "HOLD" else "yellow"
        self.console.print(
            f"[{color}]decision[/{color}] action={action} conf={confidence:.3f} "
            f"edge={edge:.3f} remaining={remaining_sec}s reason={reason}"
        )

    def order(self, text: str) -> None:
        self.console.print(f"[green]{text}[/green]")

    def warn(self, text: str) -> None:
        self.console.print(f"[yellow]{text}[/yellow]")

    def error(self, text: str) -> None:
        self.console.print(f"[red]{text}[/red]")

    def summary(self, text: str) -> None:
        self.console.print(f"[bold cyan]{text}[/bold cyan]")
