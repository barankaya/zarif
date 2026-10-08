"""Node implementations for the edit pipeline graph."""

from __future__ import annotations

import sys

from rich.console import Console

from ..diffing import render_diff, unified_diff_text
from ..io_utils import make_backup, protect_blocks, restore_blocks, write_target
from .prompts import EXTRACT_CRITICAL_PROMPT, PROPOSE_EDIT_PROMPT, VERIFY_RETENTION_PROMPT
from .schemas import CriticalItemsResult, RetentionReport
from .state import ZarifState

# stderr'e yaz: MCP sunucusu stdio üzerinden JSON-RPC konuşur, stdout'a karışmamalı.
console = Console(file=sys.stderr)


def _format_items(items: list[dict]) -> str:
    if not items:
        return "(yok)"
    return "\n".join(f"- [{i['id']}] ({i['category']}) {i['text']}" for i in items)


def parse_node(state: ZarifState) -> dict:
    protected_text, mapping = protect_blocks(state["raw_original_text"])
    return {
        "working_text": protected_text,
        "protected_map": mapping,
        "attempt": 0,
        "warnings": [],
    }


def make_extract_critical_node(editor_model):
    chain = EXTRACT_CRITICAL_PROMPT | editor_model.with_structured_output(CriticalItemsResult)

    def node(state: ZarifState) -> dict:
        result: CriticalItemsResult = chain.invoke({"document": state["working_text"]})
        return {"critical_items": [item.model_dump() for item in result.items]}

    return node


def make_propose_edit_node(editor_model):
    chain = PROPOSE_EDIT_PROMPT | editor_model

    def node(state: ZarifState) -> dict:
        attempt = state.get("attempt", 0) + 1
        missing = state.get("missing_items", [])
        retry_note = ""
        if missing:
            retry_note = (
                "Önceki denemede şu kritik maddeler kaybolmuştu, bu sürümde geri getir:\n"
                f"{_format_items(missing)}\n\n"
            )
        document = state["working_text"] if attempt == 1 else state["edited_text"]
        response = chain.invoke(
            {
                "critical_items": _format_items(state["critical_items"]),
                "retry_note": retry_note,
                "document": document,
            }
        )
        content = response.content if hasattr(response, "content") else str(response)
        return {"edited_text": content, "attempt": attempt}

    return node


def make_verify_retention_node(verifier_model):
    chain = VERIFY_RETENTION_PROMPT | verifier_model.with_structured_output(RetentionReport)

    def node(state: ZarifState) -> dict:
        report: RetentionReport = chain.invoke(
            {
                "critical_items": _format_items(state["critical_items"]),
                "document": state["edited_text"],
            }
        )
        missing = [item for item in state["critical_items"] if item["id"] in report.missing_ids]
        return {
            "sufficient": report.sufficient and not report.missing_ids,
            "missing_items": missing,
            "new_claims": report.new_claims,
        }

    return node


def route_after_verification(state: ZarifState) -> str:
    if state.get("sufficient"):
        return "human_review"
    if state.get("attempt", 0) < state.get("max_attempts", 2):
        return "propose_edit"
    return "human_review"


def human_review_node(state: ZarifState) -> dict:
    final_text = restore_blocks(state["edited_text"], state["protected_map"])
    diff_text = unified_diff_text(state["raw_original_text"], final_text, state["file_path"])

    warnings = list(state.get("warnings", []))
    if not state.get("sufficient"):
        warnings.append("Deneme limiti doldu: bazı kritik maddeler hâlâ kayıp olabilir, dikkatli incele.")
    if state.get("new_claims"):
        warnings.append(f"Doğrulayıcı yeni iddia tespit etti: {state['new_claims']}")

    render_diff(diff_text)
    for warning in warnings:
        console.print(f"[yellow]⚠ {warning}[/yellow]")

    return {"final_text": final_text, "diff_text": diff_text, "warnings": warnings}


def write_node(state: ZarifState) -> dict:
    backup_path = make_backup(state["file_path"])
    write_target(
        path=state["file_path"],
        text=state["final_text"],
        encoding=state["encoding"],
        newline=state["newline"],
    )
    return {"backup_path": backup_path, "written": True}
