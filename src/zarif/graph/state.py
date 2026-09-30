"""LangGraph state schema for the edit pipeline."""

from __future__ import annotations

from typing import TypedDict


class ZarifState(TypedDict, total=False):
    # Set at graph invocation.
    file_path: str
    encoding: str
    newline: str
    raw_original_text: str
    max_attempts: int

    # Set by parse.
    working_text: str
    protected_map: dict[str, str]
    attempt: int
    warnings: list[str]

    # Set by extract_critical.
    critical_items: list[dict]

    # Set by propose_edit.
    edited_text: str

    # Set by verify_retention.
    sufficient: bool
    missing_items: list[dict]
    new_claims: list[str]

    # Set by human_review.
    final_text: str
    diff_text: str

    # Set by write.
    backup_path: str
    written: bool
