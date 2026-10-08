"""One-off patcher for src/zarif/mcp_server.py (CRLF-safe, ASCII anchors)."""
import pathlib

p = pathlib.Path("src/zarif/mcp_server.py")
# read_text() applies universal newlines: the file's CRLF becomes "\n" in memory.
# We build anchors with "\n" and let write_text() restore CRLF on Windows.
t = p.read_text(encoding="utf-8")
NL = "\n"


def rep(old, new, count=1):
    global t
    n = t.count(old)
    assert n == count, f"expected {count} match, got {n} for: {old[:60]!r}"
    t = t.replace(old, new)


# 1) module docstring: insert note before the closing """ of the module docstring.
# The module docstring is the first """...""" block; its closing quote is the 2nd occurrence.
first = t.find('"""')
second = t.find('"""', first + 3)
assert first != -1 and second != -1
note = (
    NL
    + "Bazı host'lar (örn. IntelliJ) sampling isteklerini tek seferde işler ve üst üste" + NL
    + "gelen isteği 'Another MCP sampling request is already in progress' ile reddeder." + NL
    + "Zarif buna karşı: (1) tüm sampling çağrılarını süreç geneli bir asyncio kilidiyle" + NL
    + "serialize eder (host'a asla eşzamanlı istek gitmez) ve (2) host 'meşgul' derse" + NL
    + "sınırlı sayıda üstel geri çekilme ile yeniden dener (bkz. `_asample`)." + NL
)
t = t[:second] + note + t[second:]

# 2) McpError import + helpers, inserted right after the Context import fallback line.
anchor = "    Context = Any  # type: ignore[assignment,misc]"
assert t.count(anchor) == 1
helpers = (
    NL
    + "try:" + NL
    + "    from mcp.shared.exceptions import McpError" + NL
    + "except ImportError:  # 'mcp' extra yoksa retry mantığı yine test edilebilsin." + NL
    + "    McpError = Exception  # type: ignore[assignment,misc]" + NL
    + NL
    + "# Bazı MCP host'ları (örn. IntelliJ) sampling isteklerini tek seferde (single-flight)" + NL
    + "# işler ve üst üste gelen ikinci isteği 'Another MCP sampling request is already in" + NL
    + "# progress' ile reddeder. Zarif'in pipeline'ı zaten sıralıdır; yine de aynı anda iki" + NL
    + "# `zarif_propose_edit` çağrısı (çift tıklama, ajan retry'si) paralel sampling'e yol" + NL
    + "# açabilir ve host'un önceki isteğe ait in-flight durumu geç temizlenebilir. Bu yüzden:" + NL
    + "# (1) tüm sampling çağrılarını süreç geneli bir kilit ile serialize ediyoruz — host'a" + NL
    + "# asla eşzamanlı istek gitmez; (2) host 'meşgul' derse sınırlı sayıda, üstel geri" + NL
    + "# çekilme ile yeniden deniyoruz." + NL
    + "_SAMPLING_MAX_ATTEMPTS = 5" + NL
    + "_SAMPLING_RETRY_BASE_DELAY = 1.0" + NL
    + "_sampling_lock: Optional[asyncio.Lock] = None" + NL
    + NL
    + NL
    + "def _get_sampling_lock() -> asyncio.Lock:" + NL
    + '    """Çalışan event loop\'a bağlı, süreç geneli sampling kilidini döndürür."""' + NL
    + "    global _sampling_lock" + NL
    + "    if _sampling_lock is None:" + NL
    + "        _sampling_lock = asyncio.Lock()" + NL
    + "    return _sampling_lock" + NL
    + NL
    + NL
    + "def _is_sampling_busy_error(exc: BaseException) -> bool:" + NL
    + "    \"\"\"Host'un 'başka bir sampling isteği zaten işleniyor' hatası mı?\"\"\"" + NL
    + "    message = str(exc).lower()" + NL
    + '    return "already in progress" in message or "another mcp sampling" in message' + NL
)
t = t.replace(anchor, anchor + helpers, 1)

# 3) _asample: wrap the single create_message call in lock + bounded retry.
# Replace only the tail block (from the create_message call to the return) — it is
# unique and avoids the system_text line which has tricky escaping.
tail_old = (
    "        result = await self.ctx.session.create_message(" + NL
    + "            messages=sampling_messages," + NL
    + "            max_tokens=self.max_tokens," + NL
    + "            system_prompt=system_text or None," + NL
    + "        )" + NL
    + "        content = result.content" + NL
    + "        return content.text if isinstance(content, TextContent) else str(content)"
)
tail_new = (
    "        # Tüm sampling çağrılarını (tüm tool'lar ve session'lar arasında) serialize et:" + NL
    + "        # host'a eşzamanlı iki sampling isteği asla gitmesin." + NL
    + "        async with _get_sampling_lock():" + NL
    + "            return await self._create_message_with_retry(" + NL
    + "                sampling_messages, system_text" + NL
    + "            )" + NL
    + NL
    + "    async def _create_message_with_retry(" + NL
    + "        self, sampling_messages: list, system_text: str" + NL
    + "    ) -> str:" + NL
    + "        from mcp.types import TextContent" + NL
    + NL
    + "        last_error: Optional[BaseException] = None" + NL
    + "        for attempt in range(_SAMPLING_MAX_ATTEMPTS):" + NL
    + "            try:" + NL
    + "                result = await self.ctx.session.create_message(" + NL
    + "                    messages=sampling_messages," + NL
    + "                    max_tokens=self.max_tokens," + NL
    + "                    system_prompt=system_text or None," + NL
    + "                )" + NL
    + "                content = result.content" + NL
    + "                return content.text if isinstance(content, TextContent) else str(content)" + NL
    + "            except McpError as exc:" + NL
    + "                if not _is_sampling_busy_error(exc) or attempt == _SAMPLING_MAX_ATTEMPTS - 1:" + NL
    + "                    raise" + NL
    + "                last_error = exc" + NL
    + "                # Host'un in-flight durumu geç temizleniyor olabilir; üstel geri çekilme." + NL
    + "                delay = _SAMPLING_RETRY_BASE_DELAY * (2 ** attempt)" + NL
    + "                await asyncio.sleep(delay)" + NL
    + "        assert last_error is not None" + NL
    + "        raise last_error"
)
rep(tail_old, tail_new)

p.write_text(t, encoding="utf-8")
print("mcp_server.py patched OK")
