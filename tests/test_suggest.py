"""`src/zarif/suggest.py` testleri.

Gerçek model/ağ gerektirmez — saf regex/stdlib, tmp_path ile izole.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zarif.suggest import Suggestion, SuggestionReport, render_suggestions, run_suggestions


def _make_project(tmp_path: Path, files: dict[str, str]) -> Path:
    """tmp_path altında dosyaları oluşturur, kök dizini döndürür."""
    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return tmp_path


def test_no_suggestions_when_instruction_covers_everything(tmp_path: Path):
    """Talimat dosyası her şeyi kapsıyorsa öneri olmamalı."""
    root = _make_project(tmp_path, {
        ".env.example": "DATABASE_URL=postgres://localhost/db\nAPI_KEY=abc123\n",
        "Makefile": "build:\n\techo build\ntest:\n\techo test\n",
    })
    instruction = (
        "# Talimat\n\n"
        "DATABASE_URL ve API_KEY ortam değişkenleri gereklidir.\n"
        "build ve test komutları Makefile'da tanımlıdır.\n"
        ".env.example dosyasına bakın.\n"
    )
    report = run_suggestions(instruction, root)
    # DATABASE_URL, API_KEY, build, test, .env.example hepsi geçiyor →
    # env-var ve build-command önerisi olmamalı.
    env_suggestions = [s for s in report.suggestions if s.category == "env-var"]
    cmd_suggestions = [s for s in report.suggestions if s.category == "build-command"]
    assert not env_suggestions
    assert not cmd_suggestions


def test_env_var_suggestion(tmp_path: Path):
    """Talimat dosyasında geçmeyen env var önerilmeli."""
    root = _make_project(tmp_path, {
        ".env.example": "DATABASE_URL=postgres://localhost/db\nSECRET_TOKEN=xyz\n",
    })
    instruction = "# Talimat\n\nDATABASE_URL gereklidir.\n"
    report = run_suggestions(instruction, root)

    env_suggestions = [s for s in report.suggestions if s.category == "env-var"]
    titles = [s.title for s in env_suggestions]
    assert "ENV: SECRET_TOKEN" in titles
    # DATABASE_URL geçiyor, önerilmemeli.
    assert "ENV: DATABASE_URL" not in titles


def test_build_command_suggestion(tmp_path: Path):
    """package.json scripts talimat dosyasında geçmiyorsa önerilmeli."""
    root = _make_project(tmp_path, {
        "package.json": json.dumps({
            "scripts": {
                "dev": "vite",
                "build": "vite build",
                "test": "vitest",
            }
        }),
    })
    instruction = "# Talimat\n\ndev komutu ile geliştirme modu başlatılır.\n"
    report = run_suggestions(instruction, root)

    cmd_suggestions = [s for s in report.suggestions if s.category == "build-command"]
    titles = [s.title for s in cmd_suggestions]
    assert "Komut: build" in titles
    assert "Komut: test" in titles
    assert "Komut: dev" not in titles  # geçiyor


def test_makefile_target_suggestion(tmp_path: Path):
    """Makefile target'ları talimat dosyasında geçmiyorsa önerilmeli."""
    root = _make_project(tmp_path, {
        "Makefile": "deploy:\n\techo deploy\ndocker-build:\n\tdocker build .\n",
    })
    instruction = "# Talimat\n\ndeploy komutu kullanılır.\n"
    report = run_suggestions(instruction, root)

    cmd_suggestions = [s for s in report.suggestions if s.category == "build-command"]
    titles = [s.title for s in cmd_suggestions]
    assert "Komut: docker-build" in titles
    assert "Komut: deploy" not in titles


def test_docker_compose_service_suggestion(tmp_path: Path):
    """docker-compose servisleri talimat dosyasında geçmiyorsa önerilmeli."""
    root = _make_project(tmp_path, {
        "docker-compose.yml": (
            "services:\n"
            "  web:\n"
            "    image: nginx\n"
            "  db:\n"
            "    image: postgres:16\n"
            "  redis:\n"
            "    image: redis:7\n"
        ),
    })
    instruction = "# Talimat\n\nweb servisi ana uygulamadır.\n"
    report = run_suggestions(instruction, root)

    svc_suggestions = [s for s in report.suggestions if s.category == "service"]
    titles = [s.title for s in svc_suggestions]
    assert "Hizmet: db" in titles
    assert "Hizmet: redis" in titles
    assert "Hizmet: web" not in titles


def test_port_suggestion(tmp_path: Path):
    """docker-compose portları talimat dosyasında geçmiyorsa önerilmeli."""
    root = _make_project(tmp_path, {
        "docker-compose.yml": (
            "services:\n"
            "  web:\n"
            "    ports:\n"
            "      - '8080:80'\n"
            "      - '3000:3000'\n"
        ),
    })
    instruction = "# Talimat\n\nUygulama 8080 portunda çalışır.\n"
    report = run_suggestions(instruction, root)

    port_suggestions = [s for s in report.suggestions if s.category == "port"]
    titles = [s.title for s in port_suggestions]
    assert "Port: 3000" in titles
    assert "Port: 8080" not in titles  # geçiyor


def test_config_file_suggestion(tmp_path: Path):
    """Proje kökündeki konfig dosyaları talimat dosyasında geçmiyorsa önerilmeli."""
    root = _make_project(tmp_path, {
        "tsconfig.json": "{}",
        ".eslintrc.json": "{}",
        ".prettierrc": "{}",
    })
    instruction = "# Talimat\n\ntsconfig.json kullanılır.\n"
    report = run_suggestions(instruction, root)

    cfg_suggestions = [s for s in report.suggestions if s.category == "config-file"]
    titles = [s.title for s in cfg_suggestions]
    assert "Konfig: .eslintrc.json" in titles
    assert "Konfig: .prettierrc" in titles
    assert "Konfig: tsconfig.json" not in titles


def test_empty_project_no_suggestions(tmp_path: Path):
    """Boş projede öneri olmamalı."""
    report = run_suggestions("# Talimat\n\nHiçbir şey yok.\n", tmp_path)
    assert not report.has_suggestions


def test_render_suggestions_empty(tmp_path: Path):
    """Öneri yoksa render bilgilendirici mesaj döndürmeli."""
    report = SuggestionReport(instruction_file="test.md")
    output = render_suggestions(report)
    assert "bulunamadı" in output


def test_render_suggestions_with_items(tmp_path: Path):
    """Öneriler varsa kategorilere ayrılmış markdown dönmeli."""
    report = SuggestionReport(instruction_file="test.md")
    report.suggestions.append(Suggestion(
        category="env-var",
        title="ENV: FOO",
        detail="test",
        source_file=".env.example",
        suggested_text="- `FOO`",
    ))
    report.suggestions.append(Suggestion(
        category="port",
        title="Port: 9999",
        detail="test",
        source_file="docker-compose.yml",
        suggested_text="- Port 9999",
    ))
    output = render_suggestions(report)
    assert "Ortam değişkenleri" in output
    assert "Portlar" in output
    assert "ENV: FOO" in output
    assert "Port: 9999" in output
    assert "2 öneri" in output


def test_suggestion_dataclass_fields():
    """Suggestion dataclass alanları doğru olmalı."""
    s = Suggestion(
        category="env-var",
        title="ENV: TEST",
        detail="detay",
        source_file=".env",
        suggested_text="- `TEST`",
    )
    assert s.category == "env-var"
    assert s.title == "ENV: TEST"
    assert s.detail == "detay"
    assert s.source_file == ".env"
    assert s.suggested_text == "- `TEST`"


def test_report_has_suggestions_property():
    report = SuggestionReport(instruction_file="x.md")
    assert not report.has_suggestions
    report.suggestions.append(Suggestion("env-var", "T", "D", "S", "X"))
    assert report.has_suggestions
