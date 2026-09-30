"""Unified diff generation and terminal rendering."""

from __future__ import annotations

import difflib

from rich.console import Console
from rich.text import Text

console = Console()


def unified_diff_text(original: str, edited: str, filename: str) -> str:
    original_lines = original.splitlines(keepends=True)
    edited_lines = edited.splitlines(keepends=True)
    diff = difflib.unified_diff(
        original_lines,
        edited_lines,
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
    )
    return "".join(diff)


def render_diff(diff_text: str) -> None:
    if not diff_text.strip():
        console.print("[dim]Değişiklik yok.[/dim]")
        return
    for line in diff_text.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            console.print(Text(line, style="bold"))
        elif line.startswith("+"):
            console.print(Text(line, style="green"))
        elif line.startswith("-"):
            console.print(Text(line, style="red"))
        elif line.startswith("@@"):
            console.print(Text(line, style="cyan"))
        else:
            console.print(Text(line, style="dim"))
