"""Multi-file instruction hierarchy discovery and cross-file duplicate detection.

Pure/deterministic: no LLM, no network. Answers the gap identified in spec.md §9
item 1 — Zarif's `edit`/`audit` commands work on a single file, but real projects
often have a whole hierarchy of instruction files (global + repo + subdirectory)
with the same rule copy-pasted at multiple levels.
"""

from __future__ import annotations

import difflib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .tokens import count_tokens

INSTRUCTION_FILENAMES = {
    "claude.md",
    "claude.local.md",
    "agents.md",
    "skill.md",
    ".cursorrules",
}

SKIP_DIR_NAMES = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
}

# Minimum normalized paragraph length (characters) to be considered for
# duplicate detection — short paragraphs (headings, single short lines)
# produce too many false positives.
DUPLICATE_MIN_LEN = 40
DUPLICATE_SIMILARITY_THRESHOLD = 0.9


@dataclass
class InstructionFile:
    path: Path
    depth: int  # directory depth relative to scan root (0 = root itself)
    tokens: int


@dataclass
class DuplicateFinding:
    paragraph_preview: str
    shallow_file: Path
    shallow_depth: int
    deep_file: Path
    deep_depth: int
    similarity: float


def _is_instruction_file(name: str) -> bool:
    lname = name.lower()
    if lname in INSTRUCTION_FILENAMES:
        return True
    return lname.endswith("copilot-instructions.md")


def discover_instruction_files(root: Path) -> list[InstructionFile]:
    """Recursively find known instruction filenames under `root`.

    Depth 0 = files directly in `root`; deeper files are in subdirectories.
    """
    root = Path(root).resolve()
    found: list[InstructionFile] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]
        current_dir = Path(dirpath)
        depth = len(current_dir.relative_to(root).parts)
        for name in filenames:
            if not _is_instruction_file(name):
                continue
            file_path = current_dir / name
            try:
                text = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            found.append(InstructionFile(path=file_path, depth=depth, tokens=count_tokens(text)))
    found.sort(key=lambda f: (f.depth, str(f.path)))
    return found


def _paragraphs(text: str) -> list[str]:
    """Split into whitespace-normalized paragraphs, dropping short/noisy ones."""
    raw_paragraphs = re.split(r"\n\s*\n", text)
    result = []
    for p in raw_paragraphs:
        normalized = " ".join(p.split())
        if len(normalized) >= DUPLICATE_MIN_LEN:
            result.append(normalized)
    return result


def find_cross_file_duplicates(files: list[InstructionFile]) -> list[DuplicateFinding]:
    """Find near-duplicate paragraphs across a set of instruction files.

    For each pair, the shallower (or equal-depth) file is treated as the
    "more general" one — the recommendation is always to keep the content
    there and remove it from the deeper/more specific file.
    """
    paragraphs_by_path: dict[Path, list[str]] = {}
    for f in files:
        try:
            text = f.path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        paragraphs_by_path[f.path] = _paragraphs(text)

    findings: list[DuplicateFinding] = []
    for i, a in enumerate(files):
        for b in files[i + 1 :]:
            if a.path == b.path:
                continue
            shallow, deep = (a, b) if a.depth <= b.depth else (b, a)
            for para_a in paragraphs_by_path.get(shallow.path, []):
                for para_b in paragraphs_by_path.get(deep.path, []):
                    ratio = difflib.SequenceMatcher(None, para_a, para_b).ratio()
                    if ratio >= DUPLICATE_SIMILARITY_THRESHOLD:
                        preview = para_a[:80] + ("…" if len(para_a) > 80 else "")
                        findings.append(
                            DuplicateFinding(
                                paragraph_preview=preview,
                                shallow_file=shallow.path,
                                shallow_depth=shallow.depth,
                                deep_file=deep.path,
                                deep_depth=deep.depth,
                                similarity=round(ratio, 2),
                            )
                        )
    return findings
