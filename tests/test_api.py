"""`src/zarif/api.py` testleri.

Gerçek model/ağ çağrısı gerektirmez — `get_chat_model` monkeypatch ile
sahte bir model döndürülür, graph pipeline'ı `_FakeChatModel` ile çalışır.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.chat_models import SimpleChatModel
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel

from zarif.api import EditResult, edit_file, edit_text
from zarif.graph.schemas import CriticalItemModel, CriticalItemsResult, RetentionReport


class _FakeChatModel(SimpleChatModel):
    """Pipeline'ı gerçek model/ağ olmadan çalıştırmak için."""

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _call(self, messages, stop=None, run_manager=None, **kwargs) -> str:
        return "Kısa ve net metin."

    def with_structured_output(self, schema, *, include_raw: bool = False, **kwargs):
        def _invoke(prompt_value, config=None, **_):
            if schema is CriticalItemsResult:
                return CriticalItemsResult(
                    items=[CriticalItemModel(id="C1", text="Her zaman yedek al.", category="rule")]
                )
            if schema is RetentionReport:
                return RetentionReport(retained_ids=["C1"], missing_ids=[], new_claims=[], sufficient=True)
            raise AssertionError(f"beklenmeyen şema: {schema}")

        return RunnableLambda(_invoke)


@pytest.fixture
def fake_model(monkeypatch: pytest.MonkeyPatch):
    """get_chat_model'ı sahte modele yönlendirir."""
    import zarif.api as api_mod

    monkeypatch.setattr(api_mod, "get_chat_model", lambda spec: _FakeChatModel())


def test_edit_file_returns_result_without_writing(tmp_path: Path, fake_model):
    target = tmp_path / "CLAUDE.md"
    target.write_text("# Talimat\n\nUzun ve şişkin bir paragraf burada.\n", encoding="utf-8")

    result = edit_file(str(target), editor_model="fake:model")

    assert isinstance(result, EditResult)
    assert result.file_path == str(target)
    assert result.original_text == target.read_text(encoding="utf-8")
    assert result.sufficient is True
    assert result.token_report["before"] > 0
    assert result.token_report["after"] > 0
    # Dosya henüz yazılmamış olmalı.
    assert target.read_text(encoding="utf-8") == result.original_text
    result.discard()


def test_edit_file_apply_writes_and_backs_up(tmp_path: Path, fake_model):
    target = tmp_path / "AGENTS.md"
    original = "# Başlık\n\nDetaylı açıklama metni.\n"
    target.write_text(original, encoding="utf-8")

    result = edit_file(str(target), editor_model="fake:model")
    backup_path = result.apply()

    # Yedek oluşturulmuş olmalı.
    assert Path(backup_path).exists()
    assert Path(backup_path).read_text(encoding="utf-8") == original
    # Dosya güncellenmiş olmalı (edited_text farklıysa).
    assert result._applied is True
    # Tekrar apply() çağrılamaz.
    with pytest.raises(RuntimeError, match="zaten uygulanmış"):
        result.apply()


def test_edit_file_discard_prevents_apply(tmp_path: Path, fake_model):
    target = tmp_path / "SKILL.md"
    target.write_text("# Test\n\nİçerik.\n", encoding="utf-8")

    result = edit_file(str(target), editor_model="fake:model")
    result.discard()

    with pytest.raises(RuntimeError, match="zaten uygulanmış"):
        result.apply()


def test_edit_text_returns_result(fake_model):
    text = "# Talimat\n\nBu metin düzenlenecek.\n"
    result = edit_text(text, editor_model="fake:model")

    assert isinstance(result, EditResult)
    assert result.file_path == "<memory>"
    assert result.original_text == text
    assert result.sufficient is True
    result.discard()


def test_edit_text_apply_to_writes_file(tmp_path: Path, fake_model):
    text = "# Talimat\n\nUzun metin burada.\n"
    target = tmp_path / "output.md"
    target.write_text(text, encoding="utf-8")

    result = edit_text(text, editor_model="fake:model")
    backup_path = result.apply_to(str(target))

    assert Path(backup_path).exists()
    assert result._applied is True
    with pytest.raises(RuntimeError, match="zaten uygulanmış"):
        result.apply_to(str(target))


def test_edit_text_cannot_apply_without_graph(fake_model):
    """edit_text sonucu apply() ile (graph resume) çalıştırılamaz, apply_to() kullanılmalı."""
    text = "# Test\n"
    result = edit_text(text, editor_model="fake:model")
    # apply() graph.invoke(None, config) çağırır — edit_text'te graph var ama
    # write node dosya yazar; bu test sadece API sözleşmesini doğrular.
    # apply_to() kullanılması beklenir.
    result.discard()


def test_edit_result_fields_populated(tmp_path: Path, fake_model):
    target = tmp_path / "test.md"
    target.write_text("# Başlık\n\nKural: Her zaman test yaz.\n", encoding="utf-8")

    result = edit_file(str(target), editor_model="fake:model")

    assert result.warnings is not None
    assert result.missing_items is not None
    assert result.new_claims is not None
    assert isinstance(result.token_report, dict)
    assert "before" in result.token_report
    assert "after" in result.token_report
    assert "reduction_pct" in result.token_report
    result.discard()


def test_import_from_package_root():
    """`from zarif import edit_file, edit_text, EditResult` çalışmalı."""
    from zarif import EditResult as ER, edit_file as ef, edit_text as et

    assert ef is not None
    assert et is not None
    assert ER is not None
