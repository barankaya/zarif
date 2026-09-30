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
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Optional

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import SimpleChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.runnables import RunnableLambda
from pydantic import ConfigDict

from .graph import build_graph
from .io_utils import find_latest_backup, read_source, restore_from_backup
from .models import get_chat_model
from .tokens import token_report

try:
    from mcp.server.fastmcp import Context
except ImportError:  # 'mcp' extra yüklü değilse modül yine de import edilebilsin.
    Context = Any  # type: ignore[assignment,misc]


class SamplingChatModel(SimpleChatModel):
    """MCP host'un modelini `sampling/createMessage` ile LangChain chat modeli gibi sunar."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    ctx: Any = None
    loop: Any = None
    max_tokens: int = 2000

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
        result = await self.ctx.session.create_message(
            messages=sampling_messages,
            max_tokens=self.max_tokens,
            system_prompt=system_text or None,
        )
        content = result.content
        return content.text if isinstance(content, TextContent) else str(content)

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


# session_id -> {"graph": ..., "config": ...}; `zarif_propose_edit` ile doldurulur,
# `zarif_apply_edit` ile tüketilir. Sunucu süreci hayatta olduğu sürece bellekte tutulur.
_SESSIONS: dict[str, dict[str, Any]] = {}


def register_tools(server: Any) -> None:
    """FastMCP `@server.tool()` dekoratörleriyle zarif araçlarını tanımlar."""

    @server.tool()
    async def zarif_propose_edit(
        ctx: Context,
        file_path: str,
        max_attempts: int = 2,
        editor_model: Optional[str] = None,
    ) -> dict:
        """Bir markdown talimat dosyasını sadeleştirir, diff döner (dosyaya YAZMAZ).

        `editor_model` verilmezse bu MCP host'unun kendi modeli (sampling) kullanılır;
        onay için dönen `session_id`'yi `zarif_apply_edit`'e verin.
        """
        source = read_source(file_path)
        loop = asyncio.get_running_loop()

        if editor_model:
            model = get_chat_model(editor_model)
        else:
            model = SamplingChatModel(ctx=ctx, loop=loop)
        graph = build_graph(model, model)

        session_id = str(uuid.uuid4())
        config = {"configurable": {"thread_id": session_id}}
        initial_state = {
            "file_path": file_path,
            "encoding": source.encoding,
            "newline": source.newline,
            "raw_original_text": source.text,
            "max_attempts": max_attempts,
        }
        result = await asyncio.to_thread(graph.invoke, initial_state, config)
        _SESSIONS[session_id] = {"graph": graph, "config": config}

        report = token_report(source.text, result.get("final_text", source.text))
        return {
            "session_id": session_id,
            "diff": result.get("diff_text", ""),
            "sufficient": result.get("sufficient", False),
            "warnings": result.get("warnings", []),
            "token_report": report,
        }

    @server.tool()
    async def zarif_apply_edit(session_id: str) -> dict:
        """`zarif_propose_edit` ile önerilen değişikliği onaylar ve dosyaya yazar."""
        session = _SESSIONS.pop(session_id, None)
        if session is None:
            raise ValueError(f"Bilinmeyen session_id: {session_id!r} (önce zarif_propose_edit çağırın)")
        result = await asyncio.to_thread(session["graph"].invoke, None, session["config"])
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


def create_server() -> Any:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("MCP sunucusu için 'mcp' paketi gerekli: pip install 'zarif[mcp]'") from exc

    server = FastMCP("zarif")
    register_tools(server)
    return server


def run_server() -> None:
    create_server().run()
