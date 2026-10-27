"""`zarif fix` — audit'in kesin bulduğu sorunların deterministik, LLM'siz düzeltmesi.

Yalnızca çıkarma/maskeleme yapar, yeni bilgi eklemez (Prensip 1):
  - secret değerleri `<REDACTED>` ile maskelenir,
  - config dosyası tarafından zaten uygulanan (ölü) kurallar, tek satırlık ve
    alt maddesi olmayan liste maddesiyse silinir.
Geri kalan her şey (tablo/paragraf içindeki ölü kural, private key, iç IP, çelişki,
bayat referans) yalnızca raporlanır; dosya insan onayı olmadan yazılmaz (Prensip 3).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .audit import DeadRuleFinding, SecretFinding, find_dead_rules, redact_secrets, scan_for_secrets

_LIST_ITEM_RE = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+\S")


@dataclass
class FixResult:
    fixed_text: str
    removed_dead_rules: list[DeadRuleFinding] = field(default_factory=list)
    redacted_secrets: list[SecretFinding] = field(default_factory=list)
    manual: list[str] = field(default_factory=list)  # otomatik düzeltilemeyen bulgular

    @property
    def changed(self) -> bool:
        return bool(self.removed_dead_rules or self.redacted_secrets)


def _fenced_line_numbers(lines: list[str]) -> set[int]:
    fenced: set[int] = set()
    fence: Optional[str] = None
    for i, line in enumerate(lines, start=1):
        marker = line.strip()[:3]
        if marker in ("```", "~~~"):
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
            fenced.add(i)
        elif fence is not None:
            fenced.add(i)
    return fenced


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _is_removable_bullet(lines: list[str], idx: int, fenced: set[int]) -> bool:
    """Single-line list item outside code fences, with no more-indented children after it."""
    if idx + 1 in fenced or not _LIST_ITEM_RE.match(lines[idx]):
        return False
    for following in lines[idx + 1:]:
        if not following.strip():
            continue
        return _indent(following) <= _indent(lines[idx])
    return True


def apply_safe_fixes(text: str, project_root: Path) -> FixResult:
    """Return the fixed text plus what was changed and what still needs manual attention."""
    redacted_text, redacted = redact_secrets(text)
    lines = redacted_text.split("\n")
    fenced = _fenced_line_numbers(lines)

    removed: list[DeadRuleFinding] = []
    manual: list[str] = []
    drop: set[int] = set()
    seen_lines: set[int] = set()
    for finding in find_dead_rules(text, project_root):
        if finding.line_no in seen_lines:
            continue
        seen_lines.add(finding.line_no)
        if _is_removable_bullet(lines, finding.line_no - 1, fenced):
            drop.add(finding.line_no)
            removed.append(finding)
        else:
            manual.append(
                f"satır {finding.line_no}: ölü kural otomatik silinmedi (liste maddesi değil/alt maddeli) — {finding.rule_snippet[:60]}"
            )

    remaining_secrets = scan_for_secrets(redacted_text)
    for s in remaining_secrets:
        manual.append(f"satır {s.line_no}: {s.kind} otomatik maskelenemedi, elle kontrol edin")

    fixed = "\n".join(line for i, line in enumerate(lines, start=1) if i not in drop)
    return FixResult(fixed_text=fixed, removed_dead_rules=removed, redacted_secrets=redacted, manual=manual)
