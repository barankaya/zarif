"""`src/zarif/mcp_server.py` testleri.

`mcp` paketi kurulu olmadan da çalışabilmeleri için gerçek FastMCP yerine
basit bir sahte sunucu (`_FakeServer`) kullanıyoruz — `register_tools()`
zaten `mcp` paketini import etmiyor (yalnızca `create_server()` ediyor),
bu yüzden bu testler `mcp` extra'sı kurulu olmasa da çalışır.

Sampling/thread-bridging (`SamplingChatModel._call`/`_asample`) gerçek bir
MCP host ve event loop gerektirdiği için burada test edilmiyor; onun yerine
`with_structured_output()`'ın JSON ayrıştırma mantığı ve `editor_model`
açıkça verildiğinde sampling'e hiç girmeyen kod yolu test ediliyor.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from langchain_core.language_models.chat_models import SimpleChatModel
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel

from zarif import mcp_server
from zarif.graph.schemas import CriticalItemModel, CriticalItemsResult, RetentionReport
from zarif.mcp_server import SamplingChatModel, register_tools


class _FakeServer:
    """`FastMCP`'nin `@server.tool()` dekoratörünü taklit eder; fonksiyonu
    olduğu gibi (sarmalamadan) `registered` sözlüğüne kaydeder."""

    def __init__(self) -> None:
        self.registered: dict[str, object] = {}

    def tool(self):
        def _decorator(fn):
            self.registered[fn.__name__] = fn
            return fn

        return _decorator


class _Item(BaseModel):
    name: str
    ok: bool


class _FakeChatModel(SimpleChatModel):
    """propose_edit graph'ını gerçek bir model/ağ çağrısı olmadan çalıştırmak için."""

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _call(self, messages, stop=None, run_manager=None, **kwargs) -> str:
        return "Basitleştirilmiş metin."

    def with_structured_output(self, schema, *, include_raw: bool = False, **kwargs):
        def _invoke(prompt_value, config=None, **_):
            if schema is CriticalItemsResult:
                return CriticalItemsResult(
                    items=[CriticalItemModel(id="C1", text="Sipariş numarasını her zaman iste.", category="rule")]
                )
            if schema is RetentionReport:
                return RetentionReport(retained_ids=["C1"], missing_ids=[], new_claims=[], sufficient=True)
            raise AssertionError(f"beklenmeyen şema: {schema}")

        return RunnableLambda(_invoke)


def test_sampling_chat_model_with_structured_output_parses_json(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        SamplingChatModel, "_call", lambda self, messages, stop=None, run_manager=None, **kw: '{"name": "widget", "ok": true}'
    )

    model = SamplingChatModel()
    structured = model.with_structured_output(_Item)
    result = structured.invoke([HumanMessage(content="ignored")])

    assert isinstance(result, _Item)
    assert result.name == "widget"
    assert result.ok is True


def test_zarif_apply_edit_unknown_session_raises(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(mcp_server, "_SESSIONS", {})
    server = _FakeServer()
    register_tools(server)

    with pytest.raises(ValueError, match="Bilinmeyen session_id"):
        asyncio.run(server.registered["zarif_apply_edit"]("does-not-exist"))


def test_zarif_propose_edit_with_explicit_model_skips_sampling(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    sample = tmp_path / "sample.md"
    sample.write_text("# Kurallar\nSipariş numarasını her zaman iste.\n", encoding="utf-8")

    monkeypatch.setattr(mcp_server, "get_chat_model", lambda spec: _FakeChatModel())

    server = _FakeServer()
    register_tools(server)

    result = asyncio.run(
        server.registered["zarif_propose_edit"](ctx=None, file_path=str(sample), editor_model="fake:model")
    )

    assert result["sufficient"] is True
    assert "session_id" in result
    assert "token_report" in result
    assert result["session_id"] in mcp_server._SESSIONS


def test_zarif_discard_edit_removes_session_without_writing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(mcp_server, "_SESSIONS", {"abc": {"graph": None, "config": None}})
    server = _FakeServer()
    register_tools(server)

    result = asyncio.run(server.registered["zarif_discard_edit"]("abc"))

    assert result == {"discarded": True}
    assert "abc" not in mcp_server._SESSIONS


def test_zarif_discard_edit_unknown_session_is_a_noop():
    server = _FakeServer()
    register_tools(server)

    result = asyncio.run(server.registered["zarif_discard_edit"]("does-not-exist"))

    assert result == {"discarded": False}


def test_zarif_restore_edit_without_backup_raises(tmp_path: Path):
    target = tmp_path / "doc.md"
    target.write_text("content", encoding="utf-8")

    server = _FakeServer()
    register_tools(server)

    with pytest.raises(ValueError, match="Yedek bulunamadı"):
        asyncio.run(server.registered["zarif_restore_edit"](str(target)))


def test_zarif_restore_edit_restores_latest_backup(tmp_path: Path):
    from zarif.io_utils import make_backup

    target = tmp_path / "doc.md"
    target.write_text("original", encoding="utf-8")
    backup_path = make_backup(str(target))
    target.write_text("changed", encoding="utf-8")

    server = _FakeServer()
    register_tools(server)

    result = asyncio.run(server.registered["zarif_restore_edit"](str(target)))

    assert result == {"restored": True, "backup_path": backup_path}
    assert target.read_text(encoding="utf-8") == "original"
