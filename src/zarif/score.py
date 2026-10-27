"""Sağlık skoru — mevcut tüm deterministik kontrolleri tek bir 0-100 skorda birleştirir.

Mevcut modüllerin (audit, suggest, hierarchy, tokens) çıktılarını birleştirir,
her kategori için puan düşer ve toplam skoru üretir. LLM/ağ gerektirmez.

Skorlama (100'den başlar, aşağı düşer):

  Kategori                    Puan düşümü
  ─────────────────────────────────────────────────────
  Token sayısı                >500: -5, >1000: -10, >2000: -15, >4000: -20
  Ölü kural (audit)           -5/bulgu (max -15)
  Secret/PII (audit)          -10/bulgu (max -20)
  Çelişki (audit, deneysel)   -3/bulgu (max -9)
  Eksik operasyonel bilgi     -2/bulgu (max -10)
  Tekrar eden paragraf        -5/bulgu (max -15)  [sadece dizin modu]
  ─────────────────────────────────────────────────────
  Skor aralığı: 0-100 (0 = çok kötü, 100 = mükemmel)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .audit import run_audit
from .hierarchy import discover_instruction_files, find_cross_file_duplicates
from .suggest import run_suggestions
from .tokens import count_tokens


@dataclass
class ScoreCategory:
    """Tek bir skor kategorisinin detayı."""

    name: str
    label: str  # İnsan-okunur etiket
    findings: int  # Bulgu sayısı
    points_deducted: int  # Bu kategoriden düşülen puan
    max_deduction: int  # Bu kategorinin maksimum düşümü
    details: list[str] = field(default_factory=list)  # Kısa açıklama listesi


@dataclass
class ScoreReport:
    """Bir dosya (veya dizin) için tam sağlık skoru raporu."""

    target: str  # Dosya yolu veya dizin yolu
    score: int  # 0-100
    categories: list[ScoreCategory] = field(default_factory=list)
    token_count: int = 0
    is_directory: bool = False

    @property
    def grade(self) -> str:
        if self.score >= 90:
            return "A"
        if self.score >= 75:
            return "B"
        if self.score >= 60:
            return "C"
        if self.score >= 40:
            return "D"
        return "F"


def _token_deduction(tokens: int) -> tuple[int, int, list[str]]:
    """Token sayısına göre puan düşümü."""
    if tokens <= 500:
        return 0, 20, []
    if tokens <= 1000:
        return 5, 20, [f"{tokens} token (hedef: ≤500)"]
    if tokens <= 2000:
        return 10, 20, [f"{tokens} token (hedef: ≤500)"]
    if tokens <= 4000:
        return 15, 20, [f"{tokens} token (hedef: ≤500)"]
    return 20, 20, [f"{tokens} token (hedef: ≤500)"]


def score_file(text: str, project_root: Path, file_path: str = "") -> ScoreReport:
    """Tek bir talimat dosyası için sağlık skoru hesaplar.

    Args:
        text: Talimat dosyasının metni.
        project_root: Proje kök dizini (audit/suggest için).
        file_path: Dosya adı (raporda görünür).

    Returns:
        ScoreReport — 0-100 skor + kategori detayları.
    """
    categories: list[ScoreCategory] = []
    tokens = count_tokens(text)

    # 1. Token sayısı
    tok_ded, tok_max, tok_details = _token_deduction(tokens)
    categories.append(ScoreCategory(
        name="tokens",
        label="Token sayısı",
        findings=1 if tok_ded > 0 else 0,
        points_deducted=tok_ded,
        max_deduction=tok_max,
        details=tok_details,
    ))

    # 2. Audit (ölü kural, secret, çelişki)
    audit = run_audit(text, project_root)

    dead_ded = min(len(audit.dead_rules) * 5, 15)
    categories.append(ScoreCategory(
        name="dead_rules",
        label="Ölü kural",
        findings=len(audit.dead_rules),
        points_deducted=dead_ded,
        max_deduction=15,
        details=[f"satır {d.line_no}: {d.rule_snippet[:60]}" for d in audit.dead_rules[:5]],
    ))

    secret_ded = min(len(audit.secrets) * 10, 20)
    categories.append(ScoreCategory(
        name="secrets",
        label="Secret/PII",
        findings=len(audit.secrets),
        points_deducted=secret_ded,
        max_deduction=20,
        details=[f"satır {s.line_no}: {s.kind}" for s in audit.secrets[:5]],
    ))

    contra_ded = min(len(audit.contradictions) * 3, 9)
    categories.append(ScoreCategory(
        name="contradictions",
        label="Çelişki (deneysel)",
        findings=len(audit.contradictions),
        points_deducted=contra_ded,
        max_deduction=9,
        details=[
            f'satır {c.line_a} vs {c.line_b}'
            for c in audit.contradictions[:5]
        ],
    ))

    # 3. Suggest (eksik operasyonel bilgi)
    suggest_report = run_suggestions(text, project_root)
    sugg_ded = min(len(suggest_report.suggestions) * 2, 10)
    categories.append(ScoreCategory(
        name="missing_info",
        label="Eksik operasyonel bilgi",
        findings=len(suggest_report.suggestions),
        points_deducted=sugg_ded,
        max_deduction=10,
        details=[s.title for s in suggest_report.suggestions[:5]],
    ))

    total_deduction = sum(c.points_deducted for c in categories)
    score = max(0, 100 - total_deduction)

    return ScoreReport(
        target=file_path,
        score=score,
        categories=categories,
        token_count=tokens,
        is_directory=False,
    )


def score_directory(directory: Path) -> ScoreReport:
    """Bir dizin altındaki tüm talimat dosyaları için sağlık skoru hesaplar.

    Her dosya için `score_file` çalıştırır, ayrıca dosyalar arası tekrar
    eden paragrafları da skora dahil eder.

    Args:
        directory: Taranacak kök dizin.

    Returns:
        ScoreReport — ortalama skor + kategori detayları.
    """
    root = directory.resolve()
    files = discover_instruction_files(root)
    if not files:
        return ScoreReport(target=str(root), score=100, is_directory=True)

    # Her dosya için skor
    file_scores: list[ScoreReport] = []
    for f in files:
        try:
            text = f.path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        file_scores.append(score_file(text, root, str(f.path.relative_to(root))))

    if not file_scores:
        return ScoreReport(target=str(root), score=100, is_directory=True)

    # Kategori bazında toplamla
    cat_map: dict[str, ScoreCategory] = {}
    for fs in file_scores:
        for cat in fs.categories:
            if cat.name not in cat_map:
                cat_map[cat.name] = ScoreCategory(
                    name=cat.name,
                    label=cat.label,
                    findings=0,
                    points_deducted=0,
                    max_deduction=cat.max_deduction,
                )
            existing = cat_map[cat.name]
            existing.findings += cat.findings
            existing.points_deducted += cat.points_deducted
            existing.details.extend(cat.details[:3])  # max 3 detay per file

    # Tekrar eden paragraflar
    duplicates = find_cross_file_duplicates(files)
    dup_ded = min(len(duplicates) * 5, 15)
    cat_map["duplicates"] = ScoreCategory(
        name="duplicates",
        label="Tekrar eden paragraf",
        findings=len(duplicates),
        points_deducted=dup_ded,
        max_deduction=15,
        details=[d.paragraph_preview[:60] for d in duplicates[:5]],
    )

    total_deduction = sum(c.points_deducted for c in cat_map.values())
    score = max(0, 100 - total_deduction)
    total_tokens = sum(fs.token_count for fs in file_scores)

    return ScoreReport(
        target=str(root),
        score=score,
        categories=list(cat_map.values()),
        token_count=total_tokens,
        is_directory=True,
    )


def render_score(report: ScoreReport) -> str:
    """Skoru insan-okunur metin olarak render eder."""
    lines = [
        f"Skor: {report.score}/100 ({report.grade})",
        f"{'Dizin' if report.is_directory else 'Dosya'}: {report.target}",
        f"Toplam token: {report.token_count}",
        "",
    ]

    for cat in report.categories:
        if cat.points_deducted == 0:
            status = "✓"
        else:
            status = f"-{cat.points_deducted}"
        lines.append(f"  {status:>4}  {cat.label}: {cat.findings} bulgu")
        for detail in cat.details[:3]:
            lines.append(f"         {detail}")
        if len(cat.details) > 3:
            lines.append(f"         ... ve {len(cat.details) - 3} daha")

    return "\n".join(lines)
