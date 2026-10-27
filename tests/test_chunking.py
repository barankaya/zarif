"""Bölümlü (chunk) düzenleme testleri — model/ağ gerektirmez.

Token sayısı tiktoken yoksa sezgisel hesaba düştüğü için bölümler, her iki sayım
yönteminde de aynı sonucu verecek kadar belirgin boyutlarda seçildi.
"""

from __future__ import annotations

import pytest
from langchain_core.language_models.chat_models import SimpleChatModel
from langchain_core.runnables import RunnableLambda

import zarif.api as api_mod
from zarif.api import edit_text
from zarif.chunking import split_markdown
from zarif.graph.nodes import parse_node
from zarif.graph.schemas import CriticalItemModel, CriticalItemsResult, RetentionReport
from zarif.io_utils import restore_blocks

_BODY = " ".join(["word"] * 40)
_THREE_SECTIONS = "".join(f"# S{i}\n{_BODY}\n\n" for i in (1, 2, 3))
_CHUNK_TOKENS = 60  # bir bölüm (~40-52 token) sığar, iki bölüm sığmaz


def test_split_returns_single_chunk_when_disabled_or_fits():
    assert split_markdown(_THREE_SECTIONS, None) == [_THREE_SECTIONS]
    assert split_markdown(_THREE_SECTIONS, 100_000) == [_THREE_SECTIONS]


def test_split_cuts_at_headings_and_is_lossless():
    chunks = split_markdown(_THREE_SECTIONS, _CHUNK_TOKENS)

    assert len(chunks) == 3
    assert all(c.startswith("# S") for c in chunks)
    assert "".join(chunks) == _THREE_SECTIONS


def test_split_packs_small_sections_together():
    text = "".join(f"# T{i}\nshort words here for filler\n\n" for i in range(6))

    chunks = split_markdown(text, 40)

    assert 1 < len(chunks) < 6
    assert "".join(chunks) == text


def test_split_falls_back_to_blank_lines_inside_oversized_section():
    text = "# Big\n" + "".join(f"{_BODY}\n\n" for _ in range(4))

    chunks = split_markdown(text, _CHUNK_TOKENS)

    assert len(chunks) >= 3
    assert "".join(chunks) == text


def test_parse_node_never_splits_protected_blocks_or_fenced_hashes():
    code = "```bash\n# not a heading\nmake build\n```"
    text = f"# A\n{_BODY}\n\n{code}\n\n# B\n{_BODY}\n"

    update = parse_node({"raw_original_text": text, "chunk_tokens": _CHUNK_TOKENS})

    assert "".join(update["chunks"]) == update["working_text"]
    assert len(update["chunks"]) >= 2
    assert restore_blocks(update["working_text"], update["protected_map"]) == text
    key = next(iter(update["protected_map"]))
    assert sum(key in c for c in update["chunks"]) == 1


_PROPOSE_CALLS: list[int] = []


class _ChunkFakeModel(SimpleChatModel):
    @property
    def _llm_type(self) -> str:
        return "fake"

    def _call(self, messages, stop=None, run_manager=None, **kwargs) -> str:
        _PROPOSE_CALLS.append(1)
        return "Kısa.\n"

    def with_structured_output(self, schema, *, include_raw: bool = False, **kwargs):
        def _invoke(prompt_value, config=None, **_):
            text = str(prompt_value)
            if schema is CriticalItemsResult:
                cid = "C2" if "# S2" in text else "C1"
                return CriticalItemsResult(items=[CriticalItemModel(id=cid, text="kural", category="rule")])
            if schema is RetentionReport:
                bad = "[C2]" in text
                return RetentionReport(
                    retained_ids=[] if bad else ["C1"],
                    missing_ids=["C2"] if bad else [],
                    new_claims=[],
                    sufficient=not bad,
                )
            raise AssertionError(f"beklenmeyen şema: {schema}")

        return RunnableLambda(_invoke)


@pytest.fixture
def chunk_model(monkeypatch: pytest.MonkeyPatch):
    _PROPOSE_CALLS.clear()
    monkeypatch.setattr(api_mod, "get_chat_model", lambda spec: _ChunkFakeModel())


def test_edit_text_processes_each_chunk_and_joins_results(chunk_model):
    result = edit_text(_THREE_SECTIONS, editor_model="fake:m", max_attempts=1, chunk_tokens=_CHUNK_TOKENS)

    assert len(_PROPOSE_CALLS) == 3
    assert result.edited_text == "Kısa.\n" * 3
    assert any("3 parçaya" in w for w in result.warnings)
    result.discard()


def test_edit_text_aggregates_insufficient_chunk(chunk_model):
    result = edit_text(_THREE_SECTIONS, editor_model="fake:m", max_attempts=1, chunk_tokens=_CHUNK_TOKENS)

    assert result.sufficient is False
    assert [m["id"] for m in result.missing_items] == ["C2"]
    result.discard()


def test_edit_text_without_chunking_is_single_pass(chunk_model):
    result = edit_text(_THREE_SECTIONS, editor_model="fake:m", max_attempts=1)

    assert len(_PROPOSE_CALLS) == 1
    assert not any("parçaya" in w for w in result.warnings)
    result.discard()


def test_many_chunks_do_not_hit_langgraph_recursion_limit(chunk_model):
    text = "".join(f"# H{i}\n{_BODY}\n\n" for i in range(12))

    result = edit_text(text, editor_model="fake:m", max_attempts=1, chunk_tokens=_CHUNK_TOKENS)

    assert len(_PROPOSE_CALLS) == 12
    result.discard()
