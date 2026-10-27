"""Node implementations for the edit pipeline graph."""

from __future__ import annotations

import sys

from rich.console import Console

from ..diffing import render_diff, summarize_diff, unified_diff_text
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
    from ..chunking import split_markdown

    protected_text, mapping = protect_blocks(state["raw_original_text"])
    chunks = split_markdown(protected_text, state.get("chunk_tokens"))
    return {
        "working_text": protected_text,
        "protected_map": mapping,
        "attempt": 0,
        "warnings": [],
        "chunks": chunks,
        "chunk_index": 0,
        "edited_chunks": [],
        "all_sufficient": True,
        "all_missing_items": [],
        "all_new_claims": [],
    }


def _current_chunk(state: ZarifState) -> str:
    return state["chunks"][state["chunk_index"]]


def make_extract_critical_node(editor_model):
    chain = EXTRACT_CRITICAL_PROMPT | editor_model.with_structured_output(CriticalItemsResult)

    def node(state: ZarifState) -> dict:
        result: CriticalItemsResult = chain.invoke({"document": _current_chunk(state)})
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
        max_tokens = state.get("max_tokens")
        token_budget = ""
        if max_tokens is not None:
            if len(state["chunks"]) > 1:
                from ..tokens import count_tokens

                # Bütçe parçalara orantılı dağıtılır.
                share = count_tokens(_current_chunk(state)) / max(count_tokens(state["working_text"]), 1)
                max_tokens = max(1, round(max_tokens * share))
            token_budget = (
                f"IMPORTANT: The edited document must be at most {max_tokens} tokens. "
                "Prioritize removing filler and redundancy to meet this budget while "
                "keeping every critical item above.\n\n"
            )
        document = _current_chunk(state) if attempt == 1 else state["edited_text"]
        response = chain.invoke(
            {
                "critical_items": _format_items(state["critical_items"]),
                "token_budget": token_budget,
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


def advance_chunk_node(state: ZarifState) -> dict:
    """Biten parçayı kaydeder, sonuçlarını toplar ve bir sonraki parça için durumu sıfırlar."""
    return {
        "edited_chunks": [*state["edited_chunks"], state["edited_text"]],
        "chunk_index": state["chunk_index"] + 1,
        "all_sufficient": state["all_sufficient"] and bool(state.get("sufficient")),
        "all_missing_items": [*state["all_missing_items"], *state.get("missing_items", [])],
        "all_new_claims": [*state["all_new_claims"], *state.get("new_claims", [])],
        "attempt": 0,
        "edited_text": "",
        "critical_items": [],
        "missing_items": [],
    }


def route_after_chunk(state: ZarifState) -> str:
    if state["chunk_index"] < len(state["chunks"]):
        return "extract_critical"
    return "human_review"


def human_review_node(state: ZarifState) -> dict:
    final_text = restore_blocks("".join(state["edited_chunks"]), state["protected_map"])
    diff_text = unified_diff_text(state["raw_original_text"], final_text, state["file_path"])

    sufficient = state["all_sufficient"]
    missing_items = state["all_missing_items"]
    new_claims = state["all_new_claims"]

    warnings = list(state.get("warnings", []))
    if len(state["chunks"]) > 1:
        warnings.append(
            f"Doküman {len(state['chunks'])} parçaya bölünerek işlendi: parçalar arası tekrar/çelişki "
            "değerlendirilemedi."
        )
    if not sufficient:
        warnings.append("Deneme limiti doldu: bazı kritik maddeler hâlâ kayıp olabilir, dikkatli incele.")
    if new_claims:
        warnings.append(f"Doğrulayıcı yeni iddia tespit etti: {new_claims}")

    # Token bütçesi kontrolü (gerçek, restore edilmiş metin üzerinde).
    max_tokens = state.get("max_tokens")
    if max_tokens is not None:
        from ..tokens import count_tokens

        actual = count_tokens(final_text)
        if actual > max_tokens:
            warnings.append(
                f"Token bütçesi aşıldı: {actual} / {max_tokens} token. "
                "Model bütçeye ulaşamadı; manuel olarak kısaltmanız gerekebilir."
            )

    render_diff(diff_text)
    for warning in warnings:
        console.print(f"[yellow]⚠ {warning}[/yellow]")

    change_summary = summarize_diff(diff_text)

    return {
        "final_text": final_text,
        "diff_text": diff_text,
        "warnings": warnings,
        "change_summary": change_summary,
        "sufficient": sufficient,
        "missing_items": missing_items,
        "new_claims": new_claims,
    }


def write_node(state: ZarifState) -> dict:
    backup_path = make_backup(state["file_path"])
    write_target(
        path=state["file_path"],
        text=state["final_text"],
        encoding=state["encoding"],
        newline=state["newline"],
    )
    return {"backup_path": backup_path, "written": True}
