"""Token counting: real tokenizer when available, whitespace-based heuristic fallback."""

from __future__ import annotations

import threading

# tiktoken downloads its BPE table (cl100k_base) on first use, over the network,
# with no timeout. On networks where that host is blocked (e.g. some corporate
# firewalls) the download hangs indefinitely instead of raising, which would stall
# everything that calls count_tokens — the CLI, the test suite, and the MCP
# `zarif_propose_edit` tool (via token_report). We therefore attempt the lookup
# once, on a daemon thread, with a bounded wait; if it does not finish in time (or
# errors out) we fall back to the heuristic and remember that choice for the
# lifetime of the process. If the BPE file is already in the local tiktoken cache
# the lookup returns instantly and the real tokenizer is used.
_ENCODER_TIMEOUT_SECONDS = 10

_encoder = None
_encoder_resolved = False
_encoder_lock = threading.Lock()


def _resolve_encoder():
    """Return the tiktoken cl100k_base encoding, or None if unavailable.

    Memoized: the (possibly network-bound) lookup happens at most once per process.
    """
    global _encoder, _encoder_resolved
    if _encoder_resolved:
        return _encoder

    with _encoder_lock:
        if _encoder_resolved:
            return _encoder

        result: dict = {"enc": None}

        def _load() -> None:
            try:
                import tiktoken

                result["enc"] = tiktoken.get_encoding("cl100k_base")
            except Exception:
                result["enc"] = None

        thread = threading.Thread(target=_load, daemon=True)
        thread.start()
        thread.join(_ENCODER_TIMEOUT_SECONDS)

        # If the thread is still alive the download is hung on a blocked host.
        # Abandon it (it is a daemon, so it cannot keep the process alive) and use
        # the heuristic for the rest of this process.
        _encoder = result["enc"]
        _encoder_resolved = True
        return _encoder


def _heuristic_tokens(text: str) -> int:
    words = text.split()
    return max(1, round(len(words) * 1.3))


def count_tokens(text: str) -> int:
    enc = _resolve_encoder()
    if enc is not None:
        return len(enc.encode(text))
    return _heuristic_tokens(text)


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
