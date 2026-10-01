"""Single-file audit: deterministic dead-rule detection, secret/PII scanning,
and best-effort contradiction hints.

No LLM, no network — pure text and config-file analysis. Closes spec.md §9
items 2-5 (dead rules, secrets, contradictions, SARIF/CI output).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - exercised only on Python 3.10
    tomllib = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Dead rule / config-overlap detection
# ---------------------------------------------------------------------------


@dataclass
class DeadRuleFinding:
    rule_snippet: str
    line_no: int
    pattern_name: str
    reason: str


def _read_editorconfig(project_root: Path) -> dict:
    path = project_root / ".editorconfig"
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    settings: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";", "[")):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            settings[key.strip().lower()] = value.strip().lower()
    return settings


def _read_json_config(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        text = re.sub(r"(?m)^\s*//.*$", "", text)  # best-effort JSONC comment strip
        return json.loads(text)
    except (OSError, json.JSONDecodeError):
        return {}


def _read_pyproject(project_root: Path) -> dict:
    path = project_root / "pyproject.toml"
    if not path.exists() or tomllib is None:
        return {}
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except (OSError, ValueError):
        return {}


def _check_editorconfig_indent(project_root: Path) -> Optional[str]:
    cfg = _read_editorconfig(project_root)
    style = cfg.get("indent_style")
    if not style:
        return None
    size = cfg.get("indent_size")
    detail = f"indent_style={style}" + (f", indent_size={size}" if size else "")
    return f".editorconfig zaten bunu uyguluyor ({detail})"


def _check_editorconfig_final_newline(project_root: Path) -> Optional[str]:
    cfg = _read_editorconfig(project_root)
    if "insert_final_newline" not in cfg:
        return None
    return f".editorconfig zaten bunu uyguluyor (insert_final_newline={cfg['insert_final_newline']})"


def _check_editorconfig_trailing_whitespace(project_root: Path) -> Optional[str]:
    cfg = _read_editorconfig(project_root)
    if "trim_trailing_whitespace" not in cfg:
        return None
    return f".editorconfig zaten bunu uyguluyor (trim_trailing_whitespace={cfg['trim_trailing_whitespace']})"


def _check_prettier_semi(project_root: Path) -> Optional[str]:
    for name in (".prettierrc", ".prettierrc.json"):
        cfg = _read_json_config(project_root / name)
        if "semi" in cfg:
            return f"{name} zaten bunu uyguluyor (semi={cfg['semi']})"
    return None


def _check_prettier_quotes(project_root: Path) -> Optional[str]:
    for name in (".prettierrc", ".prettierrc.json"):
        cfg = _read_json_config(project_root / name)
        if "singleQuote" in cfg:
            return f"{name} zaten bunu uyguluyor (singleQuote={cfg['singleQuote']})"
    return None


def _check_tsconfig_strict(project_root: Path) -> Optional[str]:
    cfg = _read_json_config(project_root / "tsconfig.json")
    strict = cfg.get("compilerOptions", {}).get("strict")
    if strict is None:
        return None
    return f"tsconfig.json zaten bunu uyguluyor (compilerOptions.strict={strict})"


def _check_python_line_length(project_root: Path) -> Optional[str]:
    cfg = _read_pyproject(project_root)
    for tool in ("black", "ruff"):
        line_length = cfg.get("tool", {}).get(tool, {}).get("line-length")
        if line_length is not None:
            return f"pyproject.toml [tool.{tool}] zaten bunu uyguluyor (line-length={line_length})"
    return None


def _check_commitlint(project_root: Path) -> Optional[str]:
    for name in ("commitlint.config.js", "commitlint.config.cjs", ".commitlintrc.json"):
        if (project_root / name).exists():
            return f"{name} zaten conventional commit kuralını uyguluyor"
    return None


@dataclass
class OverlapPattern:
    name: str
    keywords: tuple[str, ...]
    checker: Callable[[Path], Optional[str]]


OVERLAP_PATTERNS: tuple[OverlapPattern, ...] = (
    OverlapPattern(
        "indent-style",
        ("indent with spaces", "use spaces not tabs", "2-space indent", "4-space indent", "indentation"),
        _check_editorconfig_indent,
    ),
    OverlapPattern(
        "final-newline",
        ("end with a newline", "trailing newline", "final newline"),
        _check_editorconfig_final_newline,
    ),
    OverlapPattern(
        "trailing-whitespace",
        ("no trailing whitespace", "trim trailing whitespace"),
        _check_editorconfig_trailing_whitespace,
    ),
    OverlapPattern(
        "semicolons",
        ("use semicolons", "always use semicolons", "no semicolons"),
        _check_prettier_semi,
    ),
    OverlapPattern("quote-style", ("single quotes", "double quotes"), _check_prettier_quotes),
    OverlapPattern("ts-strict", ("typescript strict mode", "enable strict mode"), _check_tsconfig_strict),
    OverlapPattern(
        "line-length",
        ("line length", "max line length", "79 characters", "88 characters", "100 characters"),
        _check_python_line_length,
    ),
    OverlapPattern(
        "conventional-commits",
        ("conventional commits", "commit message format"),
        _check_commitlint,
    ),
)


def find_dead_rules(text: str, project_root: Path) -> list[DeadRuleFinding]:
    """Flag instruction lines that duplicate a rule already enforced by a config file.

    Deterministic keyword match against a small, hand-picked pattern set — no LLM.
    Recall is limited by design (only catches phrasing close to the keyword list);
    treat this as a helpful hint, not an exhaustive audit.
    """
    findings: list[DeadRuleFinding] = []
    lines = text.splitlines()
    for pattern in OVERLAP_PATTERNS:
        reason = pattern.checker(project_root)
        if reason is None:
            continue
        for i, line in enumerate(lines, start=1):
            lower = line.lower()
            if any(kw in lower for kw in pattern.keywords):
                findings.append(
                    DeadRuleFinding(rule_snippet=line.strip(), line_no=i, pattern_name=pattern.name, reason=reason)
                )
    return findings


# ---------------------------------------------------------------------------
# Secret / PII scanning
# ---------------------------------------------------------------------------


@dataclass
class SecretFinding:
    line_no: int
    kind: str
    redacted_snippet: str


_PLACEHOLDER_HINTS = ("xxx", "your-", "your_", "<", "example", "placeholder", "changeme", "dummy", "***", "insert-")


def _looks_like_placeholder(value: str) -> bool:
    lower = value.lower()
    return any(hint in lower for hint in _PLACEHOLDER_HINTS)


def _redact(match_text: str) -> str:
    if len(match_text) <= 8:
        return "***"
    return match_text[:4] + "…" + match_text[-2:]


# (kind, pattern, group index used both as the "value to placeholder-check" and to redact)
SECRET_PATTERNS: tuple[tuple[str, re.Pattern, int], ...] = (
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}"), 0),
    ("generic_api_key", re.compile(r"\b(?:sk|pk)-[A-Za-z0-9]{20,}\b"), 0),
    ("private_key_header", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"), 0),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), 0),
    (
        "generic_secret_assignment",
        re.compile(r"(?i)\b(?:api[_-]?key|secret|password|token)\s*[:=]\s*['\"]([^'\"\s]{6,})['\"]"),
        1,
    ),
    ("internal_ip", re.compile(r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), 0),
)


def scan_for_secrets(text: str) -> list[SecretFinding]:
    """Regex-based scan for accidentally committed secrets/PII in an instruction file.

    Deliberately conservative (skips obvious placeholders like `your-api-key`) to
    keep false positives low, at the cost of missing cleverly-disguised secrets.
    """
    findings: list[SecretFinding] = []
    for i, line in enumerate(text.splitlines(), start=1):
        for kind, pattern, group_idx in SECRET_PATTERNS:
            m = pattern.search(line)
            if not m:
                continue
            value = m.group(group_idx)
            if _looks_like_placeholder(value):
                continue
            findings.append(SecretFinding(line_no=i, kind=kind, redacted_snippet=_redact(m.group(0))))
    return findings


# ---------------------------------------------------------------------------
# Contradiction hints (experimental, heuristic — not LLM-based)
# ---------------------------------------------------------------------------


@dataclass
class ContradictionFinding:
    line_a: int
    text_a: str
    line_b: int
    text_b: str
    shared_words: tuple[str, ...]


_POSITIVE_MARKERS = ("always", "must", "should", "required", "use ")
_NEGATIVE_MARKERS = ("never", "don't", "do not", "avoid", "must not", "should not")
_STOPWORDS = {
    "the", "this", "that", "with", "from", "your", "have", "will", "when", "then", "each",
    "must", "always", "never", "should", "avoid", "required", "code", "file", "files",
}


def _significant_words(line: str) -> set[str]:
    words = re.findall(r"[a-zA-ZçğıöşüÇĞİÖŞÜ]{4,}", line.lower())
    return {w for w in words if w not in _STOPWORDS}


def _polarity(line: str) -> Optional[str]:
    lower = line.lower()
    if any(m in lower for m in _NEGATIVE_MARKERS):
        return "negative"
    if any(m in lower for m in _POSITIVE_MARKERS):
        return "positive"
    return None


def find_contradictions(text: str) -> list[ContradictionFinding]:
    """Best-effort, deterministic hint for possibly-contradicting rules.

    Heuristic only: flags line pairs with opposite polarity markers (always/must
    vs never/don't) that also share significant vocabulary. Expect false
    positives/negatives — this is not a substitute for an LLM-based review,
    it is a cheap, offline pre-filter.
    """
    lines = text.splitlines()
    candidates = [(i, line) for i, line in enumerate(lines, start=1) if _polarity(line)]
    findings: list[ContradictionFinding] = []
    for idx_a in range(len(candidates)):
        line_no_a, text_a = candidates[idx_a]
        pol_a = _polarity(text_a)
        words_a = _significant_words(text_a)
        if not words_a:
            continue
        for idx_b in range(idx_a + 1, len(candidates)):
            line_no_b, text_b = candidates[idx_b]
            if _polarity(text_b) == pol_a:
                continue
            words_b = _significant_words(text_b)
            if not words_b:
                continue
            shared = words_a & words_b
            union = words_a | words_b
            if len(shared) >= 2 and (len(shared) / len(union)) >= 0.3:
                findings.append(
                    ContradictionFinding(
                        line_a=line_no_a,
                        text_a=text_a.strip(),
                        line_b=line_no_b,
                        text_b=text_b.strip(),
                        shared_words=tuple(sorted(shared)),
                    )
                )
    return findings


# ---------------------------------------------------------------------------
# Unified audit + SARIF rendering
# ---------------------------------------------------------------------------


@dataclass
class AuditResult:
    dead_rules: list[DeadRuleFinding]
    secrets: list[SecretFinding]
    contradictions: list[ContradictionFinding]


def run_audit(text: str, project_root: Path) -> AuditResult:
    return AuditResult(
        dead_rules=find_dead_rules(text, project_root),
        secrets=scan_for_secrets(text),
        contradictions=find_contradictions(text),
    )


def render_sarif(result: AuditResult, file_path: str) -> dict:
    """Render an AuditResult as a minimal SARIF 2.1.0 document (GitHub Code Scanning)."""
    rules: list[dict] = []
    results: list[dict] = []
    rule_ids_seen: set[str] = set()

    def ensure_rule(rule_id: str, short_desc: str) -> None:
        if rule_id not in rule_ids_seen:
            rules.append({"id": rule_id, "shortDescription": {"text": short_desc}})
            rule_ids_seen.add(rule_id)

    def add_result(rule_id: str, level: str, message: str, line_no: int) -> None:
        results.append(
            {
                "ruleId": rule_id,
                "level": level,
                "message": {"text": message},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": file_path},
                            "region": {"startLine": max(line_no, 1)},
                        }
                    }
                ],
            }
        )

    for d in result.dead_rules:
        ensure_rule("zarif/dead-rule", "Kural zaten bir konfigürasyon dosyası tarafından uygulanıyor")
        add_result("zarif/dead-rule", "note", f"{d.rule_snippet} — {d.reason}", d.line_no)

    for s in result.secrets:
        ensure_rule("zarif/secret", "Olası hassas bilgi tespit edildi")
        add_result("zarif/secret", "error", f"Olası {s.kind}: {s.redacted_snippet}", s.line_no)

    for c in result.contradictions:
        ensure_rule("zarif/contradiction", "Olası çelişkili kural (deneysel)")
        add_result("zarif/contradiction", "warning", f'"{c.text_a}" ile "{c.text_b}" çelişebilir', c.line_a)

    return {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {"driver": {"name": "zarif", "rules": rules}},
                "results": results,
            }
        ],
    }
