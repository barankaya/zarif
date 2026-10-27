"""Zarif kütüphane API'si — CLI veya MCP olmadan, doğrudan Python kodundan kullanım.

Örnek::

    from zarif import edit_file

    result = edit_file("CLAUDE.md", editor_model="ollama:llama3.1")

    if result.diff_text.strip():
        print(result.diff_text)
        print(f"Token: {result.token_report['before']} → {result.token_report['after']}")
        if result.sufficient:
            backup = result.apply()  # yedek alır, dosyaya yazar
            print(f"Yazıldı. Yedek: {backup}")
        else:
            print("Bilgi kaybı tespit edildi, yazılmadı.")
    else:
        print("Önerilecek değişiklik yok.")
    result.discard()  # kaynakları serbest bırak
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .graph import build_graph
from .io_utils import make_backup, read_source, write_target
from .models import get_chat_model
from .tokens import token_report


@dataclass
class EditResult:
    """Bir ``edit_file`` / ``edit_text`` çağrısının sonucu (henüz diske yazılmadı).

    Attributes:
        file_path: İşlenen dosyanın yolu (``edit_text`` için sanal yol).
        original_text: Orijinal metin.
        edited_text: Önerilen düzenlenmiş metin.
        diff_text: Unified diff (boşsa değişiklik yok demektir).
        token_report: ``{"before": int, "after": int, "reduction_pct": float}``.
        sufficient: Doğrulama adımı bilgi kaybı tespit etmediyse ``True``.
        warnings: Pipeline sırasında üretilen uyarılar.
        missing_items: Kayıp kritik maddeler (sufficient=False ise dolu).
        new_claims: Orijinalde olmayan yeni iddialar (varsa).
        change_summary: Diff'in yapılandırılmış özeti (satır ekleme/silme,
            etkilenen bölümler, tek cümlelik özet). LLM gerektirmez.
    """

    file_path: str
    original_text: str
    edited_text: str
    diff_text: str
    token_report: dict
    sufficient: bool
    warnings: list[str] = field(default_factory=list)
    missing_items: list[dict] = field(default_factory=list)
    new_claims: list[str] = field(default_factory=list)
    change_summary: dict = field(default_factory=dict)

    # Internal — apply() için gerekli.
    _graph: Any = field(default=None, repr=False)
    _config: dict = field(default=None, repr=False)
    _encoding: str = field(default="utf-8", repr=False)
    _newline: str = field(default="\n", repr=False)
    _applied: bool = field(default=False, repr=False)

    def apply(self) -> str:
        """Önerilen düzenlemeyi diske yazar (önce yedek alır).

        Returns:
            Yedek dosyasının yolu.

        Raises:
            RuntimeError: Sonuç zaten uygulanmışsa veya pipeline tamamlanmamışsa.
        """
        if self._applied:
            raise RuntimeError("Sonuç zaten uygulanmış.")
        if self._graph is None or self._config is None:
            raise RuntimeError("Uygulanabilir bir pipeline durumu yok (edit_text kullanıldıysa apply() çağrılamaz).")

        # write node'unu çalıştır (interrupt_before=["write"] sonrası resume).
        final = self._graph.invoke(None, self._config)
        backup_path = final.get("backup_path", "")
        self._applied = True
        return backup_path

    def apply_to(self, target_path: str) -> str:
        """Önerilen metni *farklı bir* dosyaya yazar (yedek alır).

        ``edit_text`` ile bellek içi metin düzenledikten sonra sonucu bir dosyaya
        yazmak için kullanılır.

        Returns:
            Yedek dosyasının yolu.
        """
        if self._applied:
            raise RuntimeError("Sonuç zaten uygulanmış.")
        backup_path = make_backup(target_path)
        write_target(target_path, self.edited_text, self._encoding, self._newline)
        self._applied = True
        return backup_path

    def discard(self) -> None:
        """Kaynakları serbest bırakır (graph state vb.). Tekrar apply() çağrılamaz."""
        self._graph = None
        self._config = None
        self._applied = True


def edit_file(
    file_path: str,
    editor_model: str = "ollama:llama3.1",
    verifier_model: Optional[str] = None,
    max_attempts: int = 2,
    max_tokens: Optional[int] = None,
) -> EditResult:
    """Bir markdown talimat dosyası için düzenleme önerir (diske YAZMAZ).

    Args:
        file_path: Düzenlenecek dosyanın yolu.
        editor_model: Editör model spesifikasyonu (``"provider:model"``).
        verifier_model: Doğrulayıcı model; boşsa ``editor_model`` ile aynı.
        max_attempts: Bilgi kaybı durumunda propose_edit'e geri dönme hakkı.
        max_tokens: Düzenlenmiş metnin en fazla kaç token olacağı. ``None`` ise sınırsız.

    Returns:
        :class:`EditResult` — ``apply()`` ile diske yazılır, ``discard()`` ile
        kaynaklar serbest bırakılır.
    """
    source = read_source(file_path)

    editor = get_chat_model(editor_model)
    verifier = get_chat_model(verifier_model or editor_model)
    graph = build_graph(editor, verifier)

    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    result = graph.invoke(
        {
            "file_path": file_path,
            "encoding": source.encoding,
            "newline": source.newline,
            "raw_original_text": source.text,
            "max_attempts": max_attempts,
            "max_tokens": max_tokens,
        },
        config,
    )

    final_text = result.get("final_text", source.text)
    report = token_report(source.text, final_text)

    return EditResult(
        file_path=file_path,
        original_text=source.text,
        edited_text=final_text,
        diff_text=result.get("diff_text", ""),
        token_report=report,
        sufficient=bool(result.get("sufficient", False)),
        warnings=result.get("warnings", []),
        missing_items=result.get("missing_items", []),
        new_claims=result.get("new_claims", []),
        change_summary=result.get("change_summary", {}),
        _graph=graph,
        _config=config,
        _encoding=source.encoding,
        _newline=source.newline,
    )


def edit_text(
    text: str,
    editor_model: str = "ollama:llama3.1",
    verifier_model: Optional[str] = None,
    max_attempts: int = 2,
    max_tokens: Optional[int] = None,
    *,
    file_path: str = "<memory>",
    encoding: str = "utf-8",
    newline: str = "\n",
) -> EditResult:
    """Bellek içi metin için düzenleme önerir (dosya I/O yok).

    ``edit_file``'ın dosya okuma/yazma olmadan çalışan versiyonu. Sonucu
    ``apply_to(target_path)`` ile bir dosyaya yazabilirsiniz.

    Args:
        text: Düzenlenecek markdown metni.
        editor_model: Editör model spesifikasyonu.
        verifier_model: Doğrulayıcı model; boşsa ``editor_model`` ile aynı.
        max_attempts: Bilgi kaybı durumunda retry hakkı.
        max_tokens: Düzenlenmiş metnin en fazla kaç token olacağı. ``None`` ise sınırsız.
        file_path: Sanal dosya adı (log/diff çıktısında görünür).
        encoding: Metnin encoding'i (varsayılan utf-8).
        newline: Satır sonu karakteri (varsayılan ``"\\n"``).

    Returns:
        :class:`EditResult` — ``apply_to(path)`` ile diske yazılır.
    """
    editor = get_chat_model(editor_model)
    verifier = get_chat_model(verifier_model or editor_model)
    graph = build_graph(editor, verifier)

    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    result = graph.invoke(
        {
            "file_path": file_path,
            "encoding": encoding,
            "newline": newline,
            "raw_original_text": text,
            "max_attempts": max_attempts,
            "max_tokens": max_tokens,
        },
        config,
    )

    final_text = result.get("final_text", text)
    report = token_report(text, final_text)

    return EditResult(
        file_path=file_path,
        original_text=text,
        edited_text=final_text,
        diff_text=result.get("diff_text", ""),
        token_report=report,
        sufficient=bool(result.get("sufficient", False)),
        warnings=result.get("warnings", []),
        missing_items=result.get("missing_items", []),
        new_claims=result.get("new_claims", []),
        change_summary=result.get("change_summary", {}),
        _graph=graph,
        _config=config,
        _encoding=encoding,
        _newline=newline,
    )
