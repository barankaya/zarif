"""Prompt templates for each LLM-backed node."""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

EXTRACT_CRITICAL_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You extract the critical items from an instruction/spec document written for an AI agent. "
            "A critical item is a rule, a boundary/constraint, an acceptance criterion, or an explicit "
            "warning. Quote each item's original wording as closely as possible, do not paraphrase. "
            "Ignore filler sentences, restatements, and generic advice. "
            "The text may contain placeholder tokens like \\x00ZARIF_CODE_0\\x00 or "
            "\\x00ZARIF_TABLE_0\\x00 standing in for a protected code block or table — treat them as "
            "opaque markers, do not try to interpret what is inside them.",
        ),
        ("human", "Document:\n\n{document}"),
    ]
)

PROPOSE_EDIT_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You edit instruction documents written for AI agents. You may ONLY: remove redundant or "
            "filler text, shorten sentences without losing meaning, and reorder so critical items appear "
            "earlier. You must NEVER introduce a new claim, rule, or fact that isn't already present in "
            "the original text. Preserve the markdown structure (headers, lists). Preserve every "
            "placeholder token exactly as-is (tokens look like \\x00ZARIF_CODE_0\\x00 or "
            "\\x00ZARIF_TABLE_0\\x00) — never alter, remove, or split them, though you may move the whole "
            "token to a different position. Return only the edited document text, nothing else — no "
            "preamble, no explanation, no markdown code fence around the whole answer.",
        ),
        (
            "human",
            "Critical items that MUST remain present in some form:\n{critical_items}\n\n"
            "{retry_note}"
            "Original document:\n\n{document}",
        ),
    ]
)

VERIFY_RETENTION_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You verify whether an edited document still contains a given list of critical items, and "
            "whether it introduces any new claim not implied by that list. Be strict: if an item's "
            "meaning is not clearly and unambiguously still present, mark it missing rather than assume "
            "it's fine.",
        ),
        ("human", "Critical items:\n{critical_items}\n\nEdited document:\n\n{document}"),
    ]
)
