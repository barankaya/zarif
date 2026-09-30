"""Token counting: real tokenizer when available, whitespace-based heuristic fallback."""

from __future__ import annotations


def count_tokens(text: str) -> int:
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        words = text.split()
        return max(1, round(len(words) * 1.3))


def token_report(before: str, after: str) -> dict:
    before_count = count_tokens(before)
    after_count = count_tokens(after)
    reduction = before_count - after_count
    pct = (reduction / before_count * 100) if before_count else 0.0
    return {
        "before": before_count,
        "after": after_count,
        "reduction": reduction,
        "reduction_pct": round(pct, 1),
    }
