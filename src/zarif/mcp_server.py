"""Zarif'i bir MCP (Model Context Protocol) sunucusu olarak çalıştırır.

Amaç: `--editor-model`/`--verifier-model` için ayrı bir sağlayıcı kurulumuna
(Ollama/OpenAI vb.) gerek kalmadan zarif'i kullanabilmek. Bu sunucu modunda
editör/doğrulayıcı LLM çağrıları, MCP'nin "sampling" (`sampling/createMessage`)
yeteneğiyle bağlı olan MCP host'a (ör. VS Code Copilot Chat, Claude Desktop)
devredilir — host hangi modeli kullanıyorsa o kullanılır, zarif'in kendi API
anahtarına/yerel modele ihtiyacı kalmaz.

`--editor-model` yine de açıkça verilirse (örn. otomasyon/CI senaryosu için),
sampling yerine mevcut `get_chat_model()` sağlayıcı mekanizması kullanılır.

Not: `mcp` paketi bu ağ erişimi olmayan ortamda doğrulanamadı; `SamplingMessage`/
`create_message` imza ayrıntıları resmi MCP Python SDK dokümantasyonuna göre
yazıldı ama kurulu bir paket karşısında test edilmedi.

Bazı host'lar (örn. IntelliJ) sampling isteklerini tek seferde işler ve üst üste
gelen isteği 'Another MCP sampling request is already in progress' ile reddeder.
Zarif buna karşı: (1) tüm sampling çağrılarını süreç geneli bir asyncio kilidiyle
serialize eder (host'a asla eşzamanlı istek gitmez) ve (2) host 'meşgul' derse
sınırlı sayıda üstel geri çekilme ile yeniden dener (bkz. `_asample`).

Ayrıca bazı host'lar (yine IntelliJ dahil, bkz. docs/mcp-guide.md §9 -32001 satırı)
tek bir tool çağrısı içinde birden fazla ardışık sampling round-trip'ini kendi
tool-call zaman aşımı içinde bekleyemiyor — MCP progress notification göndermek
(bkz. `_report_progress`) bunu çözmedi (host timeout'u uzatmıyor). Bu yüzden
`zarif_propose_edit` artık HİÇBİR sampling çağrısı yapmadan anında döner; graf
`extract_critical`/`propose_edit`/`verify_retention` öncesinde durur
(`build_graph(..., step_by_step=True)`) ve yeni `zarif_continue_edit(session_id)`
aracı grafı tek seferde en fazla BİR sampling round-trip'i kadar ilerletir. Host
(ajanı), `status` alanı `"ready_to_apply"` olana kadar `zarif_continue_edit`'i
tekrar tekrar çağırır — böylece her tek tool çağrısı en fazla bir round-trip
kadar sürer, host'un kendi modeli (sampling) hâlâ kullanılır.
Aynı adım-adım desen, CLI'daki `zarif benchmark` komutunun (gömülü örnek
dosyalar üzerinde token azalması/bilgi korunumu ölçümü) MCP karşılığı olan
`zarif_benchmark_start`/`zarif_benchmark_continue`/`zarif_benchmark_discard`
araçları için de kullanılıyor — hiçbir dosyaya yazmaz, host'un modelini
kullanır. Aşağı akış görev skorlaması (`--downstream`) henüz bu araçlara
taşınmadı (bkz. docs/mcp-guide.md §5)."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any, Optional

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import SimpleChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.runnables import RunnableLambda
from pydantic import ConfigDict

from .graph import build_graph
from .io_utils import ensure_utf8_streams, find_latest_backup, read_source, restore_from_backup
from .models import get_chat_model
from .tokens import token_report

_SAMPLES_DIR = Path(__file__).parent / "benchmark" / "samples"

try:
    from mcp.server.fastmcp import Context
except ImportError:  # 'mcp' extra yüklü değilse modül yine de import edilebilsin.
    Context = Any  # type: ignore[assignment,misc]

# Bazı MCP host'ları (örn. IntelliJ) sampling isteklerini tek seferde (single-flight)
# işler ve üst üste gelen ikinci isteği 'Another MCP sampling request is already in
# progress' ile reddeder. Zarif'in pipeline'ı zaten sıralıdır; yine de aynı anda iki
# `zarif_propose_edit` çağrısı (çift tıklama, ajan retry'si) paralel sampling'e yol
# açabilir ve host'un önceki isteğe ait in-flight durumu geç temizlenebilir. Bu yüzden:
# (1) tüm sampling çağrılarını süreç geneli bir kilit ile serialize ediyoruz — host'a
# asla eşzamanlı istek gitmez; (2) host 'meşgul' derse sınırlı sayıda, üstel geri
# çekilme ile yeniden deniyoruz.
_SAMPLING_MAX_ATTEMPTS = 5
_SAMPLING_RETRY_BASE_DELAY = 1.0
_sampling_lock: Optional[asyncio.Lock] = None


def _get_sampling_lock() -> asyncio.Lock:
    """Çalışan event loop'a bağlı, süreç geneli sampling kilidini döndürür."""
    global _sampling_lock
    if _sampling_lock is None:
        _sampling_lock = asyncio.Lock()
    return _sampling_lock


def _is_sampling_busy_error(exc: BaseException) -> bool:
    """Host'un 'başka bir sampling isteği zaten işleniyor' hatası mı?"""
    message = str(exc).lower()
    return "already in progress" in message or "another mcp sampling" in message



class SamplingChatModel(SimpleChatModel):
    """MCP host'un modelini `sampling/createMessage` ile LangChain chat modeli gibi sunar."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    ctx: Any = None
    loop: Any = None
    max_tokens: int = 2000
    # Bir `zarif_propose_edit` çağrısı 3-5 ardışık sampling round-trip'i yapabilir;
    # her biri host'un zaman aşımını tetikleyebilecek kadar sürebilir. Host'a MCP
    # progress notification göndererek "hâlâ çalışıyoruz" sinyali veriyoruz — bazı
    # host'lar (IntelliJ dahil) bunu görünce tool-call zaman aşımını sıfırlar/uzatır.
    sampling_call_count: int = 0

    @property
    def _llm_type(self) -> str:
        return "mcp-sampling"

    def _call(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> str:
        # nodes.py senkron `.invoke()` çağırıyor; burası bir worker thread'de
        # çalışır (bkz. asyncio.to_thread kullanımı aşağıda), bu yüzden asıl
        # sampling çağrısını ana event loop'a threadsafe olarak devrediyoruz.
        future = asyncio.run_coroutine_threadsafe(self._asample(messages), self.loop)
        return future.result()

    async def _asample(self, messages: list[BaseMessage]) -> str:
        from mcp.types import SamplingMessage, TextContent

        system_text = "\n\n".join(str(m.content) for m in messages if m.type == "system")
        sampling_messages = [
            SamplingMessage(
                role="assistant" if m.type == "ai" else "user",
                content=TextContent(type="text", text=str(m.content)),
            )
            for m in messages
            if m.type != "system"
        ]
        # Tüm sampling çağrılarını (tüm tool'lar ve session'lar arasında) serialize et:
        # host'a eşzamanlı iki sampling isteği asla gitmesin.
        async with _get_sampling_lock():
            return await self._create_message_with_retry(
                sampling_messages, system_text
            )

    async def _create_message_with_retry(
        self, sampling_messages: list, system_text: str
    ) -> str:
        from mcp.types import TextContent

        last_error: Optional[BaseException] = None
        for attempt in range(_SAMPLING_MAX_ATTEMPTS):
            self.sampling_call_count += 1
            await self._report_progress(
                f"MCP host modeline sampling çağrısı #{self.sampling_call_count} gönderiliyor "
                f"(deneme {attempt + 1}/{_SAMPLING_MAX_ATTEMPTS})..."
            )
            try:
                result = await self.ctx.session.create_message(
                    messages=sampling_messages,
                    max_tokens=self.max_tokens,
                    system_prompt=system_text or None,
                )
                content = result.content
                return content.text if isinstance(content, TextContent) else str(content)
            except Exception as exc:
                # Host/SDK sürümüne göre "meşgul" hatası McpError yerine başka bir
                # exception tipiyle (ör. sarmalanmış bir RuntimeError) de gelebilir;
                # tipe değil mesaj içeriğine göre eşleştiriyoruz (bkz. _is_sampling_busy_error).
                if not _is_sampling_busy_error(exc) or attempt == _SAMPLING_MAX_ATTEMPTS - 1:
                    raise
                last_error = exc
                # Host'un in-flight durumu geç temizleniyor olabilir; üstel geri çekilme.
                delay = _SAMPLING_RETRY_BASE_DELAY * (2 ** attempt)
                await asyncio.sleep(delay)
        assert last_error is not None
        raise last_error

    async def _report_progress(self, message: str) -> None:
        """Host'a 'hâlâ çalışıyoruz' sinyali gönderir; progress token yoksa (ya da ctx yoksa) sessizce atlanır."""
        if self.ctx is None:
            return
        try:
            await self.ctx.report_progress(self.sampling_call_count, message=message)
        except Exception:
            pass  # progress bildirimi best-effort'tur, ana akışı asla bozmamalı.

    def with_structured_output(self, schema: Any, *, include_raw: bool = False, **kwargs: Any) -> Any:
        # MCP sampling düz metin tabanlıdır (yerel tool-calling/JSON şeması
        # zorlaması yok); şemayı prompta ekleyip yanıtı elle ayrıştırıyoruz.
        parser: PydanticOutputParser = PydanticOutputParser(pydantic_object=schema)
        format_instructions = parser.get_format_instructions()

        def _invoke(prompt_value: Any, config: Any = None, **_: Any) -> Any:
            base_messages = (
                prompt_value.to_messages() if hasattr(prompt_value, "to_messages") else list(prompt_value)
            )
            messages = list(base_messages) + [
                HumanMessage(
                    content=(
                        "Yanıtını SADECE bu JSON şemasına uyacak şekilde ver, başka metin ekleme:\n"
                        f"{format_instructions}"
                    )
                )
            ]
            ai_message: AIMessage = self.invoke(messages, config=config)
            parsed = parser.parse(str(ai_message.content))
            return {"raw": ai_message, "parsed": parsed} if include_raw else parsed

        return RunnableLambda(_invoke)


# session_id -> {"graph": ..., "config": ..., "source_text": ...}; `zarif_propose_edit` ile
# doldurulur, adım adım `zarif_continue_edit` ile ilerletilir, `zarif_apply_edit` ile tüketilir.
# Sunucu süreci hayatta olduğu sürece bellekte tutulur.
_SESSIONS: dict[str, dict[str, Any]] = {}


def _progress_payload(graph: Any, config: dict, session_id: str, source_text: str) -> dict:
    """Bir oturumun şu anki durumunu host'un anlayacağı şekilde özetler.

    Graf "write" öncesinde durduysa (ya da tamamen bittiyse) düzenleme hazırdır;
    aksi halde bir sonraki adım bir sampling round-trip'i gerektirir.
    """
    snapshot = graph.get_state(config)
    next_nodes = snapshot.next
    if not next_nodes or next_nodes[0] == "write":
        values = snapshot.values
        report = token_report(source_text, values.get("final_text", source_text))
        return {
            "session_id": session_id,
            "status": "ready_to_apply",
            "diff": values.get("diff_text", ""),
            "sufficient": values.get("sufficient", False),
            "warnings": values.get("warnings", []),
            "token_report": report,
        }
    return {
        "session_id": session_id,
        "status": "in_progress",
        "next_step": next_nodes[0],
    }


# run_id -> {"graph", "files", "file_index", "max_attempts", "results", "config", "source_text"};
# `zarif_benchmark_start` ile doldurulur, adım adım `zarif_benchmark_continue` ile ilerletilir.
# Hiçbir dosyaya YAZMAZ (CLI'daki `zarif benchmark` ile aynı davranış).
_BENCHMARK_SESSIONS: dict[str, dict[str, Any]] = {}


async def _start_benchmark_file(session: dict[str, Any]) -> None:
    """Sıradaki örnek dosya için grafı başlatır (sadece yerel "parse" çalışır)."""
    sample = session["files"][session["file_index"]]
    source = read_source(str(sample))
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    await asyncio.to_thread(
        session["graph"].invoke,
        {
            "file_path": str(sample),
            "encoding": source.encoding,
            "newline": source.newline,
            "raw_original_text": source.text,
            "max_attempts": session["max_attempts"],
        },
        config,
    )
    session["config"] = config
    session["source_text"] = source.text


def _benchmark_payload(run_id: str, session: dict[str, Any]) -> dict:
    return {
        "run_id": run_id,
        "status": "in_progress",
        "file": session["files"][session["file_index"]].name,
        "file_index": session["file_index"] + 1,
        "total_files": len(session["files"]),
        "completed": session["results"],
    }


def register_tools(server: Any) -> None:
    """FastMCP `@server.tool()` dekoratörleriyle zarif araçlarını tanımlar."""

    @server.tool()
    async def zarif_propose_edit(
        ctx: Context,
        file_path: str,
        max_attempts: int = 2,
        editor_model: Optional[str] = None,
    ) -> dict:
        """Bir markdown talimat dosyası için düzenleme oturumu başlatır (henüz model çağırmaz).

        Bu araç ANINDA döner — hiçbir sampling/LLM çağrısı yapmaz (bazı host'lar, ör.
        IntelliJ, tek bir tool çağrısı içinde birden fazla ardışık sampling round-trip'i
        zaman aşımına uğratıyor). Dönen `session_id` ile `status="in_progress"` olduğu
        sürece `zarif_continue_edit`'i tekrar tekrar çağırın — her çağrı EN FAZLA bir
        sampling round-trip'i yapar. `status="ready_to_apply"` olduğunda `diff` hazırdır;
        onaylarsanız `zarif_apply_edit`'i, reddederseniz `zarif_discard_edit`'i çağırın.
        """
        source = read_source(file_path)
        loop = asyncio.get_running_loop()

        if editor_model:
            model = get_chat_model(editor_model)
        else:
            model = SamplingChatModel(ctx=ctx, loop=loop)
        graph = build_graph(model, model, step_by_step=True)

        session_id = str(uuid.uuid4())
        config = {"configurable": {"thread_id": session_id}}
        initial_state = {
            "file_path": file_path,
            "encoding": source.encoding,
            "newline": source.newline,
            "raw_original_text": source.text,
            "max_attempts": max_attempts,
        }
        # Sadece "parse" çalışır (yerel, LLM'siz) ve "extract_critical" öncesinde durur.
        await asyncio.to_thread(graph.invoke, initial_state, config)
        _SESSIONS[session_id] = {"graph": graph, "config": config, "source_text": source.text}

        return _progress_payload(graph, config, session_id, source.text)

    @server.tool()
    async def zarif_continue_edit(session_id: str) -> dict:
        """Bir `zarif_propose_edit` oturumunu bir sonraki adıma kadar ilerletir.

        Her çağrı en fazla bir sampling round-trip'i yapar (extract_critical,
        propose_edit veya verify_retention adımlarından biri). `status` alanı
        `"in_progress"` olduğu sürece bu aracı tekrar çağırın; `"ready_to_apply"`
        olunca `diff` hazırdır (bkz. `zarif_apply_edit`/`zarif_discard_edit`).
        """
        session = _SESSIONS.get(session_id)
        if session is None:
            raise ValueError(f"Bilinmeyen session_id: {session_id!r} (önce zarif_propose_edit çağırın)")
        graph, config = session["graph"], session["config"]
        await asyncio.to_thread(graph.invoke, None, config)
        return _progress_payload(graph, config, session_id, session["source_text"])

    @server.tool()
    async def zarif_apply_edit(session_id: str) -> dict:
        """`zarif_propose_edit`/`zarif_continue_edit` ile onaylanan değişikliği dosyaya yazar.

        Önce `status="ready_to_apply"` olana kadar `zarif_continue_edit`'i çağırmış
        olmanız gerekir.
        """
        session = _SESSIONS.get(session_id)
        if session is None:
            raise ValueError(f"Bilinmeyen session_id: {session_id!r} (önce zarif_propose_edit çağırın)")
        graph, config = session["graph"], session["config"]
        next_nodes = graph.get_state(config).next
        if next_nodes and next_nodes[0] != "write":
            raise ValueError(
                "Oturum henüz hazır değil (status='in_progress'); önce zarif_continue_edit'i "
                "'ready_to_apply' olana kadar çağırın."
            )
        _SESSIONS.pop(session_id, None)
        result = await asyncio.to_thread(graph.invoke, None, config)
        return {"written": result.get("written", False), "backup_path": result.get("backup_path")}

    @server.tool()
    async def zarif_discard_edit(session_id: str) -> dict:
        """`zarif_propose_edit` ile açılan, uygulanmamış bir oturumu bellekten temizler.

        Host, kullanıcı öneriyi reddettiğinde bu aracı çağırmalı — aksi halde
        oturum, sunucu süreci hayatta olduğu sürece bellekte birikmeye devam eder.
        """
        session = _SESSIONS.pop(session_id, None)
        return {"discarded": session is not None}

    @server.tool()
    async def zarif_restore_edit(file_path: str) -> dict:
        """Bir dosyayı en son alınan `.bak` yedeğinden geri yükler (CLI'daki `zarif restore` ile aynı davranış)."""
        backup_path = find_latest_backup(file_path)
        if backup_path is None:
            raise ValueError(f"Yedek bulunamadı: {file_path}.bak")
        restore_from_backup(backup_path, file_path)
        return {"restored": True, "backup_path": backup_path}

    @server.tool()
    async def zarif_benchmark_start(
        ctx: Context,
        editor_model: Optional[str] = None,
        max_attempts: int = 2,
    ) -> dict:
        """Gömülü örnek dosyalar üzerinde token azalması/bilgi korunumu ölçümünü başlatır.

        `zarif_propose_edit` ile aynı mantık: ANINDA döner, hiçbir dosyaya yazmaz
        (CLI'daki `zarif benchmark` ile aynı). `status="in_progress"` olduğu sürece
        `zarif_benchmark_continue`'u tekrar tekrar çağırın; her çağrı en fazla bir
        sampling round-trip'i yapar. `editor_model` verilmezse bu MCP host'unun
        kendi modeli (sampling) kullanılır.
        """
        files = sorted(_SAMPLES_DIR.glob("*.md"))
        if not files:
            raise ValueError(f"Örnek dosya bulunamadı: {_SAMPLES_DIR}")

        loop = asyncio.get_running_loop()
        model = get_chat_model(editor_model) if editor_model else SamplingChatModel(ctx=ctx, loop=loop)
        graph = build_graph(model, model, step_by_step=True)

        run_id = str(uuid.uuid4())
        session: dict[str, Any] = {
            "graph": graph,
            "files": files,
            "file_index": 0,
            "max_attempts": max_attempts,
            "results": [],
        }
        _BENCHMARK_SESSIONS[run_id] = session
        await _start_benchmark_file(session)
        return _benchmark_payload(run_id, session)

    @server.tool()
    async def zarif_benchmark_continue(run_id: str) -> dict:
        """Bir `zarif_benchmark_start` oturumunu bir sonraki adıma kadar ilerletir.

        Her çağrı en fazla bir sampling round-trip'i yapar. `status` `"in_progress"`
        olduğu sürece tekrar çağırın; `"done"` olunca `results` tüm örnek dosyaların
        token_report/sufficient sonuçlarını içerir (bkz. `zarif benchmark --json`).
        """
        session = _BENCHMARK_SESSIONS.get(run_id)
        if session is None:
            raise ValueError(f"Bilinmeyen run_id: {run_id!r} (önce zarif_benchmark_start çağırın)")

        graph, config = session["graph"], session["config"]
        await asyncio.to_thread(graph.invoke, None, config)
        next_nodes = graph.get_state(config).next
        if next_nodes and next_nodes[0] != "write":
            return _benchmark_payload(run_id, session)

        # Bu dosya "write" öncesinde durdu (insan onayı burada gereksiz, hiçbir
        # zaman yazmıyoruz) — sonucu kaydet ve bir sonraki dosyaya geç.
        values = graph.get_state(config).values
        sample_name = session["files"][session["file_index"]].name
        report = token_report(session["source_text"], values.get("final_text", session["source_text"]))
        session["results"].append(
            {"name": sample_name, "tokens": report, "sufficient": bool(values.get("sufficient"))}
        )

        session["file_index"] += 1
        if session["file_index"] >= len(session["files"]):
            _BENCHMARK_SESSIONS.pop(run_id, None)
            return {"run_id": run_id, "status": "done", "results": session["results"]}

        await _start_benchmark_file(session)
        return _benchmark_payload(run_id, session)

    @server.tool()
    async def zarif_benchmark_discard(run_id: str) -> dict:
        """Yarım kalmış bir `zarif_benchmark_start` oturumunu bellekten temizler."""
        session = _BENCHMARK_SESSIONS.pop(run_id, None)
        return {"discarded": session is not None}


def create_server() -> Any:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("MCP sunucusu için 'mcp' paketi gerekli: pip install 'zarif[mcp]'") from exc

    server = FastMCP("zarif")
    register_tools(server)
    return server


def run_server() -> None:
    # stdout'a DOKUNMA: FastMCP stdio transport'u JSON-RPC mesajlarını oradan yazıyor.
    # Sadece stderr'i UTF-8'e zorluyoruz (rich Console'ların hepsi stderr'e yazıyor,
    # bkz. graph/nodes.py ve diffing.py) — Windows'ta legacy codepage crash'ini önler.
    ensure_utf8_streams(include_stdout=False)
    create_server().run()
