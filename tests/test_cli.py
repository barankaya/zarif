"""`src/zarif/cli.py` komut kablolamasının smoke testleri.

Gerçek model çağrısı gerektiren `edit`/`benchmark` komutlarının tam akışı
burada test edilmiyor (spec.md'nin "model çağrısı gerektiren testler yok"
kuralı); yalnızca CLI'nin doğru şekilde kurulduğu ve `serve` komutunun
doğru fonksiyonu çağırdığı doğrulanıyor.
"""

from __future__ import annotations

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
