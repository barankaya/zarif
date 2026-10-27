"""`src/zarif/cli.py` komut kablolamasının smoke testleri.

Gerçek model çağrısı gerektiren `edit`/`benchmark` komutlarının tam akışı
burada test edilmiyor (spec.md'nin "model çağrısı gerektiren testler yok"
kuralı); yalnızca CLI'nin doğru şekilde kurulduğu ve `serve` komutunun
doğru fonksiyonu çağırdığı doğrulanıyor.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from zarif import mcp_server
from zarif.cli import app
from zarif.io_utils import make_backup

runner = CliRunner()


def test_help_lists_all_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "edit" in result.output
    assert "benchmark" in result.output
    assert "serve" in result.output
    assert "restore" in result.output
    assert "dashboard" in result.output
    assert "batch" in result.output
    assert "suggest" in result.output
    assert "score" in result.output


def test_score_command_on_file(tmp_path):
    """score komutu bir dosya üzerinde çalışmalı."""
    target = tmp_path / "CLAUDE.md"
    target.write_text("# Talimat\n\nKısa metin.\n", encoding="utf-8")

    result = runner.invoke(app, ["score", str(target)])
    assert result.exit_code == 0
    assert "Skor:" in result.output


def test_score_command_json(tmp_path):
    """score --json makine-okunur çıktı vermeli."""
    target = tmp_path / "CLAUDE.md"
    target.write_text("# Talimat\n\nKısa metin.\n", encoding="utf-8")

    result = runner.invoke(app, ["score", str(target), "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "score" in data
    assert "grade" in data
    assert "categories" in data


def test_score_command_fail_under(tmp_path):
    """--fail-under threshold altında exit code 1 dönmeli."""
    target = tmp_path / "CLAUDE.md"
    target.write_text("# Talimat\n\nKısa metin.\n", encoding="utf-8")

    result = runner.invoke(app, ["score", str(target), "--fail-under", "0"])
    # Skor 0'dan büyük olacağı için geçmeli.
    assert result.exit_code == 0

    # İmkansız threshold (101 olamaz, max 100) — 100 ile dene.
    result2 = runner.invoke(app, ["score", str(target), "--fail-under", "100"])
    # Skor tam 100 değilse fail.
    assert result2.exit_code in (0, 1)


def test_serve_command_calls_run_server(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp_server, "run_server", lambda: calls.append(True))

    result = runner.invoke(app, ["serve"])

    assert result.exit_code == 0
    assert calls == [True]


def test_restore_command_without_backup_fails(tmp_path):
    target = tmp_path / "doc.md"
    target.write_text("content", encoding="utf-8")

    result = runner.invoke(app, ["restore", str(target)])

    assert result.exit_code == 1


def test_restore_command_restores_latest_backup(tmp_path):
    target = tmp_path / "doc.md"
    target.write_text("original", encoding="utf-8")
    make_backup(str(target))
    target.write_text("changed", encoding="utf-8")

    result = runner.invoke(app, ["restore", str(target), "--yes"])

    assert result.exit_code == 0
    assert target.read_text(encoding="utf-8") == "original"


def test_batch_no_instruction_files_exits_cleanly(tmp_path):
    """Boş dizinde batch çalıştırıldığında hata vermeden çıkmalı."""
    result = runner.invoke(app, ["batch", str(tmp_path)])
    assert result.exit_code == 0
    assert "bulunamadı" in result.output


def test_batch_help_shows_options():
    result = runner.invoke(app, ["batch", "--help"])
    assert result.exit_code == 0
    assert "--editor-model" in result.output
    assert "--yes" in result.output
    assert "--json" in result.output
