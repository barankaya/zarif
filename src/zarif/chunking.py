"""Büyük markdown dokümanlarını başlık sınırlarından parçalara böler.

Bölme kayıpsızdır: `"".join(split_markdown(text, n)) == text`. `parse` düğümü bunu
code-block/tablo placeholder'ları uygulandıktan SONRA çağırır, bu yüzden çit içindeki
`#` satırları başlık sanılmaz ve korunan bloklar asla ikiye bölünmez.
"""

from __future__ import annotations

import re
from typing import Optional

from .tokens import count_tokens

_BEFORE_HEADING_RE = re.compile(r"(?m)^(?=#{1,6}\s)")
_AFTER_BLANK_LINE_RE = re.compile(r"(?<=\n\n)")


def split_markdown(text: str, max_tokens: Optional[int]) -> list[str]:
    """Metni ~`max_tokens` token'lık parçalara böler (başlık sınırı öncelikli).

    Tek bir bölüm limiti aşıyorsa boş satırlardan bölünür; tek bir paragraf limiti
    aşıyorsa olduğu gibi tek parça kalır (cümle ortasından asla bölünmez).
    `max_tokens` None ya da metin zaten sığıyorsa `[text]` döner.
    """
    if max_tokens is None or count_tokens(text) <= max_tokens:
        return [text]

    units: list[str] = []
    for section in _BEFORE_HEADING_RE.split(text):
        if not section:
            continue
        if count_tokens(section) > max_tokens:
            units.extend(u for u in _AFTER_BLANK_LINE_RE.split(section) if u)
        else:
            units.append(section)

    chunks: list[str] = []
    current = ""
    current_tokens = 0
    for unit in units:
        unit_tokens = count_tokens(unit)
        if current and current_tokens + unit_tokens > max_tokens:
            chunks.append(current)
            current, current_tokens = "", 0
        current += unit
        current_tokens += unit_tokens
    if current:
        chunks.append(current)
    return chunks
