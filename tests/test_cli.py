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

runner = CliRunner()


def test_help_lists_all_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "edit" in result.output
    assert "benchmark" in result.output
    assert "serve" in result.output


def test_serve_command_calls_run_server(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp_server, "run_server", lambda: calls.append(True))

    result = runner.invoke(app, ["serve"])

    assert result.exit_code == 0
    assert calls == [True]
