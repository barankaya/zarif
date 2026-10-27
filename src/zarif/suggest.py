"""Gotcha mining / zenginleştirme önerisi — Prensip 1'e uygun "öneri modu".

Kaynak koddan (Makefile, package.json, docker-compose.yml, .env.example,
Dockerfile, pyproject.toml vb.) talimat dosyasında eksik kalan operasyonel
bilgileri deterministik olarak tespit eder ve *öneri* olarak gösterir.

**Asla dosyaya yazmaz.** Kullanıcı önerileri elle kopyalayıp yapıştırır.
Bu, Prensip 1'i ("sadece çıkar/taşı/sadeleştir, yeni bilgi ekleme") ihlal
etmez çünkü zarif dosyayı değiştirmez, sadece öneri üretir.

LLM veya ağ erişimi gerektirmez — saf regex/stdlib.
"""

from __future__ import annotations

import json
import re
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Suggestion:
    """Tek bir zenginleştirme önerisi."""

    category: str  # "env-var", "build-command", "service", "port", "config-file"
    title: str  # Kısa başlık (ör. "ENV: DATABASE_URL")
    detail: str  # Açıklama (ör. "docker-compose.yml'de tanımlı, talimat dosyasında geçmiyor.")
    source_file: str  # Bilginin bulunduğu kaynak dosya (göreceli yol)
    suggested_text: str  # Talimat dosyasına eklenmesi önerilen metin (markdown)


@dataclass
class SuggestionReport:
    """Bir talimat dosyası için tüm öneriler."""

    instruction_file: str
    suggestions: list[Suggestion] = field(default_factory=list)

    @property
    def has_suggestions(self) -> bool:
        return len(self.suggestions) > 0


# ---------------------------------------------------------------------------
# Kaynak dosya tarama yardımcıları
# ---------------------------------------------------------------------------

_ENV_VAR_RE = re.compile(
    r"""
    (?:
        os\.environ\.get\(\s*['"](?P<py1>[A-Z_][A-Z0-9_]*)['"]
      | os\.environ\[\s*['"](?P<py2>[A-Z_][A-Z0-9_]*)['"]
      | os\.getenv\(\s*['"](?P<py3>[A-Z_][A-Z0-9_]*)['"]
      | process\.env\.(?P<js1>[A-Z_][A-Z0-9_]*)
      | \bENV\s+(?P<docker>[A-Z_][A-Z0-9_]*)          # Dockerfile ENV
      | ^\s*(?P<envfile>[A-Z_][A-Z0-9_]*)\s*=          # .env / .env.example
    )
    """,
    re.VERBOSE | re.MULTILINE,
)

_PORT_RE = re.compile(r"\b(?:port|PORT)\s*[:=]\s*(\d{2,5})\b")
_DOCKER_PORT_RE = re.compile(r"^\s*-\s*['\"]?(\d+):(\d+)['\"]?", re.MULTILINE)


def _read_if_exists(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return None


def _find_env_vars(project_root: Path) -> list[tuple[str, str]]:
    """(env_var_name, source_file) çiftlerini döndürür."""
    found: list[tuple[str, str]] = []
    seen: set[str] = set()

    candidates = [
        project_root / ".env.example",
        project_root / ".env",
        project_root / "docker-compose.yml",
        project_root / "docker-compose.yaml",
        project_root / "Dockerfile",
        project_root / "Makefile",
    ]
    # Ayrıca kaynak kod dosyalarından da tara (shallow, max 3 seviye).
    for ext in ("*.py", "*.js", "*.ts", "*.go", "*.rs"):
        for p in project_root.rglob(ext):
            rel = p.relative_to(project_root)
            if len(rel.parts) <= 3 and not any(
                part in {"node_modules", ".venv", "venv", "__pycache__", ".git", "dist", "build"}
                for part in rel.parts
            ):
                candidates.append(p)

    for path in candidates:
        if not path.is_file():
            continue
        text = _read_if_exists(path)
        if text is None:
            continue
        rel = str(path.relative_to(project_root))
        for m in _ENV_VAR_RE.finditer(text):
            name = next((v for v in m.groups() if v), None)
            if name and name not in seen:
                seen.add(name)
                found.append((name, rel))
    return found


def _find_build_commands(project_root: Path) -> list[tuple[str, str, str]]:
    """(command_name, command, source_file) üçlülerini döndürür."""
    found: list[tuple[str, str, str]] = []

    # Makefile
    makefile = project_root / "Makefile"
    text = _read_if_exists(makefile)
    if text:
        for m in re.finditer(r"^(?P<target>[a-z][a-z0-9_-]*)\s*:", text, re.MULTILINE):
            target = m.group("target")
            if target not in {"all", "clean", "install", "deps", "dependencies"}:
                found.append((target, target, "Makefile"))

    # package.json scripts
    pkg = project_root / "package.json"
    text = _read_if_exists(pkg)
    if text:
        try:
            data = json.loads(text)
            scripts = data.get("scripts", {})
            for name, cmd in scripts.items():
                if name not in {"postinstall", "preinstall"}:
                    found.append((name, cmd, "package.json"))
        except json.JSONDecodeError:
            pass

    # pyproject.toml [tool.*] — sadece build/test komutları
    pyproject = project_root / "pyproject.toml"
    text = _read_if_exists(pyproject)
    if text:
        for m in re.finditer(r"^\[tool\.([a-z_-]+)\]", text, re.MULTILINE):
            tool = m.group(1)
            if tool in {"pytest", "ruff", "black", "mypy", "isort"}:
                found.append((tool, f"pyproject.toml [{tool}]", "pyproject.toml"))

    return found


def _find_services(project_root: Path) -> list[tuple[str, str]]:
    """(service_name, source_file) çiftlerini döndürür (docker-compose)."""
    found: list[tuple[str, str]] = []
    for name in ("docker-compose.yml", "docker-compose.yaml"):
        path = project_root / name
        text = _read_if_exists(path)
        if text is None:
            continue
        # services: altındaki top-level anahtarları bul
        in_services = False
        service_indent = 0
        for line in text.splitlines():
            stripped = line.strip()
            if re.match(r"^services\s*:", line):
                in_services = True
                continue
            if in_services:
                if stripped and not line.startswith(" ") and not line.startswith("\t"):
                    in_services = False
                    continue
                m = re.match(r"^  (\S+)\s*:", line)
                if m:
                    svc = m.group(1)
                    if svc not in {"image", "build", "ports", "volumes", "environment", "depends_on", "networks"}:
                        found.append((svc, name))
        break  # ilk bulunan dosyayı kullan
    return found


def _find_ports(project_root: Path) -> list[tuple[int, str]]:
    """(port, source_file) çiftlerini döndürür."""
    found: list[tuple[int, str]] = []
    seen: set[int] = set()

    for name in ("docker-compose.yml", "docker-compose.yaml", "Dockerfile"):
        path = project_root / name
        text = _read_if_exists(path)
        if text is None:
            continue
        for m in _DOCKER_PORT_RE.finditer(text):
            port = int(m.group(2))
            if port not in seen:
                seen.add(port)
                found.append((port, name))
        for m in _PORT_RE.finditer(text):
            port = int(m.group(1))
            if port not in seen:
                seen.add(port)
                found.append((port, name))

    return found


def _find_config_files(project_root: Path) -> list[str]:
    """Proje kökünde bilinen konfigürasyon dosyalarını listeler."""
    known = [
        ".env.example", ".env", "docker-compose.yml", "docker-compose.yaml",
        "Dockerfile", "Makefile", "tsconfig.json", ".eslintrc.json",
        ".eslintrc.js", ".prettierrc", ".prettierrc.json", ".editorconfig",
        "pyproject.toml", "setup.cfg", "tox.ini", ".flake8", "ruff.toml",
        "jest.config.js", "jest.config.ts", "vitest.config.ts",
        "webpack.config.js", "vite.config.ts", "next.config.js",
        "nginx.conf", "supervisord.conf",
    ]
    return [name for name in known if (project_root / name).is_file()]


# ---------------------------------------------------------------------------
# Ana fonksiyon
# ---------------------------------------------------------------------------

def run_suggestions(instruction_text: str, project_root: Path) -> SuggestionReport:
    """Talimat dosyasında eksik operasyonel bilgileri tespit eder.

    Args:
        instruction_text: Talimat dosyasının metni.
        project_root: Proje kök dizini.

    Returns:
        SuggestionReport — her öneri bir Suggestion dataclass'ıdır.
    """
    report = SuggestionReport(instruction_file="")
    lower_text = instruction_text.lower()

    # 1. Environment variables
    for var_name, source in _find_env_vars(project_root):
        if var_name.lower() not in lower_text:
            report.suggestions.append(Suggestion(
                category="env-var",
                title=f"ENV: {var_name}",
                detail=f"{source} dosyasında tanımlı, talimat dosyasında geçmiyor.",
                source_file=source,
                suggested_text=f"- `{var_name}` — {source} dosyasında tanımlı ortam değişkeni.",
            ))

    # 2. Build/run commands
    for cmd_name, cmd, source in _find_build_commands(project_root):
        if cmd_name.lower() not in lower_text:
            report.suggestions.append(Suggestion(
                category="build-command",
                title=f"Komut: {cmd_name}",
                detail=f"{source} dosyasında tanımlı, talimat dosyasında geçmiyor.",
                source_file=source,
                suggested_text=f"- `{cmd_name}` komutu ({source}): `{cmd}`",
            ))

    # 3. Services
    for svc_name, source in _find_services(project_root):
        if svc_name.lower() not in lower_text:
            report.suggestions.append(Suggestion(
                category="service",
                title=f"Hizmet: {svc_name}",
                detail=f"{source} dosyasında tanımlı, talimat dosyasında geçmiyor.",
                source_file=source,
                suggested_text=f"- `{svc_name}` servisi ({source}).",
            ))

    # 4. Ports
    for port, source in _find_ports(project_root):
        if str(port) not in instruction_text:
            report.suggestions.append(Suggestion(
                category="port",
                title=f"Port: {port}",
                detail=f"{source} dosyasında tanımlı, talimat dosyasında geçmiyor.",
                source_file=source,
                suggested_text=f"- Port {port} ({source}).",
            ))

    # 5. Config files
    for cfg in _find_config_files(project_root):
        if cfg.lower() not in lower_text:
            report.suggestions.append(Suggestion(
                category="config-file",
                title=f"Konfig: {cfg}",
                detail="Proje kökünde mevcut, talimat dosyasında geçmiyor.",
                source_file=cfg,
                suggested_text=f"- `{cfg}` konfigürasyon dosyası.",
            ))

    return report


def render_suggestions(report: SuggestionReport) -> str:
    """Önerileri insan-okunur markdown olarak render eder."""
    if not report.has_suggestions:
        return "Eksik operasyonel bilgi bulunamadı — talimat dosyası kapsamlı görünüyor."

    lines = [
        f"**{len(report.suggestions)} öneri bulundu** (talimat dosyasına eklenmesi düşünülebilir):",
        "",
    ]
    by_category: dict[str, list[Suggestion]] = {}
    for s in report.suggestions:
        by_category.setdefault(s.category, []).append(s)

    category_labels = {
        "env-var": "Ortam değişkenleri",
        "build-command": "Build/çalıştırma komutları",
        "service": "Gerekli servisler",
        "port": "Portlar",
        "config-file": "Konfigürasyon dosyaları",
    }

    for cat in ("env-var", "build-command", "service", "port", "config-file"):
        items = by_category.get(cat, [])
        if not items:
            continue
        lines.append(f"### {category_labels[cat]} ({len(items)})")
        lines.append("")
        for s in items:
            lines.append(f"- **{s.title}** — {s.detail}")
            lines.append(f"  - Önerilen metin: `{s.suggested_text}`")
        lines.append("")

    lines.append("---")
    lines.append("*Bu öneriler otomatik olarak üretilmiştir. Zarif dosyaya yazmaz;")
    lines.append("uygun gördüklerinizi elle kopyalayıp talimat dosyanıza ekleyin.*")
    return "\n".join(lines)
