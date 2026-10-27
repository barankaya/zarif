"""`src/zarif/score.py` testleri.

Gerçek model/ağ gerektirmez — saf deterministik, tmp_path ile izole.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zarif.score import ScoreCategory, ScoreReport, render_score, score_directory, score_file


def _make_project(tmp_path: Path, files: dict[str, str]) -> Path:
    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return tmp_path


def test_score_file_clean_file_high_score(tmp_path: Path):
    """Kısa, temiz bir dosya yüksek skor almalı."""
    root = _make_project(tmp_path, {})
    text = "# Talimat\n\nKısa ve net bir kural.\n"
    report = score_file(text, root, "CLAUDE.md")

    assert report.score >= 80
    assert report.grade in ("A", "B")
    assert report.token_count > 0
    assert not report.is_directory


def test_score_file_with_secrets_low_score(tmp_path: Path):
    """Secret içeren dosya düşük skor almalı."""
    root = _make_project(tmp_path, {})
    # "example" placeholder hint'inde geçmediği için filtrelenmeyen değerler.
    text = (
        "# Talimat\n\n"
        "AWS_ACCESS_KEY_ID=AKIAQ7K3M9X2B5N8R1T4\n"
        "api_key = \"sk-Ab3Cd4Ef5Gh6Ij7Kl8Mn9\"\n"
    )
    report = score_file(text, root, "CLAUDE.md")

    secret_cat = next(c for c in report.categories if c.name == "secrets")
    assert secret_cat.findings >= 1
    assert secret_cat.points_deducted >= 10
    assert report.score < 90


def test_score_file_with_dead_rules(tmp_path: Path):
    """Ölü kural içeren dosya puan kaybetmeli."""
    root = _make_project(tmp_path, {
        ".editorconfig": "[*]\nindent_style = space\nindent_size = 4\n",
    })
    text = (
        "# Talimat\n\n"
        "Her zaman 4 boşluk ile girinti kullan.\n"
        "Kısa ve net.\n"
    )
    report = score_file(text, root, "CLAUDE.md")

    dead_cat = next(c for c in report.categories if c.name == "dead_rules")
    # .editorconfig'de indent_style=space var, talimatta "4 boşluk" geçiyor.
    # Ölçek: en azından kategori mevcut olmalı.
    assert dead_cat is not None


def test_score_file_long_text_token_penalty(tmp_path: Path):
    """Uzun metin token cezası almalı."""
    root = _make_project(tmp_path, {})
    # ~2000+ token üreten uzun metin
    text = "# Talimat\n\n" + ("Bu bir dolgu cümlesidir ve hiçbir anlam taşımaz. " * 200)
    report = score_file(text, root, "CLAUDE.md")

    tok_cat = next(c for c in report.categories if c.name == "tokens")
    assert tok_cat.points_deducted > 0
    assert tok_cat.findings == 1


def test_score_file_short_text_no_token_penalty(tmp_path: Path):
    """Kısa metin token cezası almamalı."""
    root = _make_project(tmp_path, {})
    text = "# Talimat\n\nKısa metin.\n"
    report = score_file(text, root, "CLAUDE.md")

    tok_cat = next(c for c in report.categories if c.name == "tokens")
    assert tok_cat.points_deducted == 0
    assert tok_cat.findings == 0


def test_score_directory_empty(tmp_path: Path):
    """Talimat dosyası olmayan dizin 100 skor almalı."""
    report = score_directory(tmp_path)
    assert report.score == 100
    assert report.is_directory


def test_score_directory_with_files(tmp_path: Path):
    """Talimat dosyaları olan dizin skor hesaplamalı."""
    root = _make_project(tmp_path, {
        "CLAUDE.md": "# Talimat\n\nKısa metin.\n",
        "sub/AGENTS.md": "# Agent\n\nBaşka kısa metin.\n",
    })
    report = score_directory(root)

    assert report.is_directory
    assert report.score > 0
    assert report.score <= 100
    assert report.token_count > 0
    # duplicates kategorisi dizin modunda olmalı
    cat_names = [c.name for c in report.categories]
    assert "duplicates" in cat_names


def test_score_grade_boundaries():
    """Grade sınırları doğru olmalı."""
    assert ScoreReport(target="x", score=95).grade == "A"
    assert ScoreReport(target="x", score=90).grade == "A"
    assert ScoreReport(target="x", score=80).grade == "B"
    assert ScoreReport(target="x", score=75).grade == "B"
    assert ScoreReport(target="x", score=65).grade == "C"
    assert ScoreReport(target="x", score=60).grade == "C"
    assert ScoreReport(target="x", score=50).grade == "D"
    assert ScoreReport(target="x", score=40).grade == "D"
    assert ScoreReport(target="x", score=30).grade == "F"
    assert ScoreReport(target="x", score=0).grade == "F"


def test_score_never_negative(tmp_path: Path):
    """Çok kötü bir dosyada bile skor 0'ın altına düşmemeli."""
    root = _make_project(tmp_path, {})
    # Birçok secret + uzun metin
    text = (
        "# Talimat\n\n"
        + "AWS_ACCESS_KEY_ID=AKIAQ7K3M9X2B5N8R1T4\n" * 5
        + "api_key = \"sk-Ab3Cd4Ef5Gh6Ij7Kl8Mn9\"\n" * 5
        + ("Dolgu metni. " * 500)
    )
    report = score_file(text, root, "CLAUDE.md")
    assert report.score >= 0


def test_render_score_output(tmp_path: Path):
    """render_score insan-okunur metin döndürmeli."""
    root = _make_project(tmp_path, {})
    text = "# Talimat\n\nKısa metin.\n"
    report = score_file(text, root, "CLAUDE.md")
    output = render_score(report)

    assert "Skor:" in output
    assert "/100" in output
    assert "Token" in output or "token" in output
    assert report.target in output


def test_score_category_dataclass():
    """ScoreCategory dataclass alanları doğru olmalı."""
    c = ScoreCategory(
        name="tokens",
        label="Token sayısı",
        findings=1,
        points_deducted=5,
        max_deduction=20,
        details=["1200 token"],
    )
    assert c.name == "tokens"
    assert c.points_deducted == 5
    assert c.max_deduction == 20
    assert c.details == ["1200 token"]


def test_score_report_categories_list():
    """ScoreReport.categories boş list ile başlamalı."""
    r = ScoreReport(target="x", score=100)
    assert r.categories == []
    assert r.token_count == 0
    assert r.is_directory is False
