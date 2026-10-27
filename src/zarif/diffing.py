"""Unified diff generation and terminal rendering."""

from __future__ import annotations

import difflib
import sys

from rich.console import Console
from rich.text import Text

# stderr'e yaz: MCP sunucusu stdio üzerinden JSON-RPC konuşur, stdout'a karışmamalı.
console = Console(file=sys.stderr)


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


def summarize_diff(diff_text: str) -> dict:
    """Bir unified diff'i deterministik olarak analiz eder, yapılandırılmış özet üretir.

    LLM gerektirmez — saf metin analizi. Agent'ın "ne değişti"yi diff'i okumadan
    anlaması için kullanılır.

    Returns:
        dict:
            - lines_added: int
            - lines_removed: int
            - net_change: int (added - removed)
            - sections_touched: list[str] (değişiklik içeren markdown başlıkları)
            - hunks: int (değişiklik bloğu sayısı)
            - summary: str (insan-okunur tek cümlelik özet)
    """
    if not diff_text.strip():
        return {
            "lines_added": 0,
            "lines_removed": 0,
            "net_change": 0,
            "sections_touched": [],
            "hunks": 0,
            "summary": "Değişiklik yok.",
        }

    lines_added = 0
    lines_removed = 0
    hunks = 0
    current_section: str | None = None
    sections: list[str] = []
    seen_sections: set[str] = set()

    for line in diff_text.splitlines():
        if line.startswith("@@"):
            hunks += 1
            # @@ -a,b +c,d @@ <section header> biçiminde; header'ı yakala.
            parts = line.split("@@")
            if len(parts) >= 3:
                header = parts[2].strip()
                if header:
                    current_section = header
        elif line.startswith("+") and not line.startswith("+++"):
            lines_added += 1
            # Yeni satır bir başlıksa bölümü güncelle.
            content = line[1:].strip()
            if content.startswith("#"):
                current_section = content.lstrip("#").strip()
        elif line.startswith("-") and not line.startswith("---"):
            lines_removed += 1

        if current_section and current_section not in seen_sections:
            seen_sections.add(current_section)
            sections.append(current_section)

    net_change = lines_added - lines_removed

    if hunks == 0:
        summary = "Değişiklik yok."
    else:
        summary = (
            f"{hunks} bölgede {lines_added} satır eklendi, {lines_removed} satır silindi "
            f"(net {net_change:+d} satır)."
        )
        if sections:
            shown = ", ".join(sections[:3])
            more = f" ve {len(sections) - 3} daha" if len(sections) > 3 else ""
            summary += f" Etkilenen bölümler: {shown}{more}."

    return {
        "lines_added": lines_added,
        "lines_removed": lines_removed,
        "net_change": net_change,
        "sections_touched": sections,
        "hunks": hunks,
        "summary": summary,
    }
