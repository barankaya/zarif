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
import sys
import types
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

    propose = asyncio.run(
        server.registered["zarif_propose_edit"](ctx=None, file_path=str(sample), editor_model="fake:model")
    )

    # zarif_propose_edit artık anında döner (henüz hiçbir sampling round-trip'i yapmadan).
    assert propose["status"] == "in_progress"
    assert propose["next_step"] == "extract_critical"
    session_id = propose["session_id"]
    assert session_id in mcp_server._SESSIONS

    # status "ready_to_apply" olana kadar zarif_continue_edit'i tek round-trip'lik
    # adımlarla çağırıyoruz (extract_critical -> propose_edit -> verify_retention).
    result = None
    for _ in range(10):
        result = asyncio.run(server.registered["zarif_continue_edit"](session_id))
        if result["status"] == "ready_to_apply":
            break
    assert result is not None
    assert result["status"] == "ready_to_apply"
    assert result["sufficient"] is True
    assert "token_report" in result
    assert session_id in mcp_server._SESSIONS


def test_zarif_continue_edit_unknown_session_raises():
    server = _FakeServer()
    register_tools(server)

    with pytest.raises(ValueError, match="Bilinmeyen session_id"):
        asyncio.run(server.registered["zarif_continue_edit"]("does-not-exist"))


def test_zarif_apply_edit_before_ready_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    sample = tmp_path / "sample.md"
    sample.write_text("# Kurallar\nSipariş numarasını her zaman iste.\n", encoding="utf-8")

    monkeypatch.setattr(mcp_server, "get_chat_model", lambda spec: _FakeChatModel())

    server = _FakeServer()
    register_tools(server)

    propose = asyncio.run(
        server.registered["zarif_propose_edit"](ctx=None, file_path=str(sample), editor_model="fake:model")
    )

    with pytest.raises(ValueError, match="henüz hazır değil"):
        asyncio.run(server.registered["zarif_apply_edit"](propose["session_id"]))


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


def _install_fake_mcp_types(monkeypatch: pytest.MonkeyPatch) -> type:
    """`mcp.types.TextContent`'i gerçek `mcp` paketi kurulu olmadan taklit eder."""

    class _FakeTextContent:
        def __init__(self, type: str, text: str) -> None:
            self.type = type
            self.text = text

    fake_types_module = types.SimpleNamespace(TextContent=_FakeTextContent, SamplingMessage=object)
    monkeypatch.setitem(sys.modules, "mcp.types", fake_types_module)
    return _FakeTextContent


def test_sampling_retry_recovers_from_busy_error_regardless_of_exception_type(
    monkeypatch: pytest.MonkeyPatch,
):
    """Host, 'meşgul' hatasını `McpError` dışında bir tiple (ör. düz `RuntimeError`)
    döndürse bile retry mantığı mesaj içeriğine bakarak yeniden denemeli."""
    fake_text_content = _install_fake_mcp_types(monkeypatch)
    monkeypatch.setattr(mcp_server, "_SAMPLING_RETRY_BASE_DELAY", 0.0)

    attempts: list[int] = []

    class _FakeSession:
        async def create_message(self, **kwargs):
            attempts.append(1)
            if len(attempts) < 3:
                raise RuntimeError("Another MCP sampling request is already in progress.")
            return types.SimpleNamespace(content=fake_text_content(type="text", text="ok"))

    ctx = types.SimpleNamespace(session=_FakeSession())
    model = SamplingChatModel(ctx=ctx)

    result = asyncio.run(model._create_message_with_retry([], ""))

    assert result == "ok"
    assert len(attempts) == 3


def test_sampling_retry_reraises_non_busy_errors_immediately(monkeypatch: pytest.MonkeyPatch):
    _install_fake_mcp_types(monkeypatch)

    class _FakeSession:
        async def create_message(self, **kwargs):
            raise RuntimeError("some unrelated failure")

    ctx = types.SimpleNamespace(session=_FakeSession())
    model = SamplingChatModel(ctx=ctx)

    with pytest.raises(RuntimeError, match="some unrelated failure"):
        asyncio.run(model._create_message_with_retry([], ""))


def test_sampling_reports_progress_before_each_attempt(monkeypatch: pytest.MonkeyPatch):
    """Host'un tool-call zaman aşımını uzatabilmesi için her sampling denemesinden önce
    bir progress bildirimi gönderilmeli (bkz. docs/mcp-guide.md §9 -32001 satırı)."""
    fake_text_content = _install_fake_mcp_types(monkeypatch)

    progress_calls: list[tuple] = []

    class _FakeSession:
        async def create_message(self, **kwargs):
            return types.SimpleNamespace(content=fake_text_content(type="text", text="ok"))

    class _FakeCtx:
        session = _FakeSession()

        async def report_progress(self, progress, total=None, message=None):
            progress_calls.append((progress, message))

    model = SamplingChatModel(ctx=_FakeCtx())

    asyncio.run(model._create_message_with_retry([], ""))

    assert progress_calls == [(1, progress_calls[0][1])]
    assert "#1" in progress_calls[0][1]


def test_sampling_progress_reporting_failure_does_not_break_the_call(monkeypatch: pytest.MonkeyPatch):
    fake_text_content = _install_fake_mcp_types(monkeypatch)

    class _FakeSession:
        async def create_message(self, **kwargs):
            return types.SimpleNamespace(content=fake_text_content(type="text", text="ok"))

    class _FakeCtx:
        session = _FakeSession()

        async def report_progress(self, progress, total=None, message=None):
            raise RuntimeError("host does not support progress")

    model = SamplingChatModel(ctx=_FakeCtx())

    assert asyncio.run(model._create_message_with_retry([], "")) == "ok"


def test_zarif_benchmark_runs_to_completion_over_all_samples(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / "a.md").write_text("# Kurallar\nSipariş numarasını her zaman iste.\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("# Kurallar\nBaşka bir kural burada.\n", encoding="utf-8")

    monkeypatch.setattr(mcp_server, "_SAMPLES_DIR", tmp_path)
    monkeypatch.setattr(mcp_server, "get_chat_model", lambda spec: _FakeChatModel())

    server = _FakeServer()
    register_tools(server)

    result = asyncio.run(
        server.registered["zarif_benchmark_start"](ctx=None, editor_model="fake:model")
    )
    assert result["status"] == "in_progress"
    assert result["total_files"] == 2
    run_id = result["run_id"]

    for _ in range(30):
        if result["status"] == "done":
            break
        result = asyncio.run(server.registered["zarif_benchmark_continue"](run_id))

    assert result["status"] == "done"
    assert {row["name"] for row in result["results"]} == {"a.md", "b.md"}
    assert all("tokens" in row and "sufficient" in row for row in result["results"])
    assert run_id not in mcp_server._BENCHMARK_SESSIONS


def test_zarif_benchmark_continue_unknown_run_id_raises():
    server = _FakeServer()
    register_tools(server)

    with pytest.raises(ValueError, match="Bilinmeyen run_id"):
        asyncio.run(server.registered["zarif_benchmark_continue"]("does-not-exist"))


def test_zarif_benchmark_discard_removes_session(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(mcp_server, "_BENCHMARK_SESSIONS", {"xyz": {}})
    server = _FakeServer()
    register_tools(server)

    result = asyncio.run(server.registered["zarif_benchmark_discard"]("xyz"))

    assert result == {"discarded": True}
    assert "xyz" not in mcp_server._BENCHMARK_SESSIONS
