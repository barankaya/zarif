"""Encoding-preserving read/write and code-block/table protection utilities."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

_BOM_UTF8 = b"\xef\xbb\xbf"
_BOM_UTF16_LE = b"\xff\xfe"
_BOM_UTF16_BE = b"\xfe\xff"

# Matches fenced code blocks (``` or ~~~), keeping the fence marker consistent via backreference.
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,}).*?\n.*?^ {0,3}\1[ \t]*$", re.DOTALL | re.MULTILINE)
# Matches consecutive markdown table rows (lines starting and ending with '|').
_TABLE_RE = re.compile(r"(?:^\|.*\|[ \t]*$\n?)+", re.MULTILINE)


def ensure_utf8_streams(*, include_stdout: bool = True, include_stderr: bool = True) -> None:
    """Windows'ta sistem codepage'i (ör. cp1254/cp857) modelin ürettiği Unicode
    karakterlerde (ör. '→') `UnicodeEncodeError` ile çökebiliyor. MCP sunucusu
    stdio üzerinden JSON-RPC konuştuğu için stdout'a DOKUNULMAMALI; sadece
    `include_stdout=False` ile çağrılmalı (bkz. mcp_server.run_server)."""
    for include, stream in ((include_stdout, sys.stdout), (include_stderr, sys.stderr)):
        if not include:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


@dataclass
class SourceFile:
    text: str  # decoded, newlines normalized to "\n"
    encoding: str
    newline: str


def detect_encoding(raw: bytes) -> str:
    if raw.startswith(_BOM_UTF8):
        return "utf-8-sig"
    if raw.startswith(_BOM_UTF16_LE) or raw.startswith(_BOM_UTF16_BE):
        return "utf-16"
    return "utf-8"


def detect_newline(text: str) -> str:
    if "\r\n" in text:
        return "\r\n"
    if "\r" in text:
        return "\r"
    return "\n"


def read_source(path: str) -> SourceFile:
    raw = Path(path).read_bytes()
    encoding = detect_encoding(raw)
    text = raw.decode(encoding)
    newline = detect_newline(text)
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return SourceFile(text=normalized, encoding=encoding, newline=newline)


def write_target(path: str, text: str, encoding: str, newline: str) -> None:
    content = text.replace("\n", newline) if newline != "\n" else text
    Path(path).write_bytes(content.encode(encoding))


def make_backup(path: str) -> str:
    p = Path(path)
    backup_path = p.with_name(p.name + ".bak")
    counter = 1
    while backup_path.exists():
        backup_path = p.with_name(f"{p.name}.bak{counter}")
        counter += 1
    backup_path.write_bytes(p.read_bytes())
    return str(backup_path)


def find_latest_backup(path: str) -> str | None:
    """Return the most recently created `.bak`/`.bakN` file for path, or None."""
    p = Path(path)
    prefix = f"{p.name}.bak"
    latest: tuple[int, Path] | None = None
    for candidate in p.parent.glob(f"{prefix}*"):
        suffix = candidate.name[len(prefix):]
        if suffix and not suffix.isdigit():
            continue
        order = int(suffix) if suffix else 0
        if latest is None or order > latest[0]:
            latest = (order, candidate)
    return str(latest[1]) if latest else None


def restore_from_backup(backup_path: str, target_path: str) -> None:
    Path(target_path).write_bytes(Path(backup_path).read_bytes())


def protect_blocks(text: str) -> tuple[str, dict[str, str]]:
    """Replace fenced code blocks and tables with placeholder tokens so an LLM edit can't touch them."""
    mapping: dict[str, str] = {}

    def _protect(match: re.Match[str], prefix: str) -> str:
        key = f"\x00ZARIF_{prefix}_{len(mapping)}\x00"
        mapping[key] = match.group(0)
        return key

    text = _FENCE_RE.sub(lambda m: _protect(m, "CODE"), text)
    text = _TABLE_RE.sub(lambda m: _protect(m, "TABLE"), text)
    return text, mapping


def restore_blocks(text: str, mapping: dict[str, str]) -> str:
    for key, value in mapping.items():
        text = text.replace(key, value)
    return text
