"""`zarif edit`, `zarif restore` and `zarif benchmark` CLI commands."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .audit import merge_sarif, render_sarif, run_audit
from .benchmark.dashboard import BenchmarkRow, format_score, render_html
from .benchmark.downstream import get_task_for, run_downstream
from .graph import build_graph
from .hierarchy import discover_instruction_files, find_cross_file_duplicates
from .io_utils import ensure_utf8_streams, find_latest_backup, read_source, restore_from_backup
from .models import get_chat_model
from .tokens import token_report

ensure_utf8_streams()  # Windows'ta legacy codepage'lerde model çıktısındaki Unicode karakterler çökmesin.

app = typer.Typer(add_completion=False, help="Zarif — talimat dosyalarını sadeleştiren CLI.")
console = Console()

_EDITOR_MODEL_HELP = "Örn. 'ollama:llama3.1' veya 'openai:gpt-4o-mini'."
_SAMPLES_DIR = Path(__file__).parent / "benchmark" / "samples"


@app.command()
def edit(
    file: Path = typer.Argument(..., exists=True, readable=True, help="Düzenlenecek markdown dosyası."),
    editor_model: str = typer.Option("ollama:llama3.1", "--editor-model", help=_EDITOR_MODEL_HELP),
    verifier_model: Optional[str] = typer.Option(
        None, "--verifier-model", help="Boş bırakılırsa editor-model ile aynısı kullanılır."
    ),
    max_attempts: int = typer.Option(2, "--max-attempts", min=1),
    max_tokens: Optional[int] = typer.Option(
        None, "--max-tokens", min=1,
        help="Düzenlenmiş metnin en fazla kaç token olacağı (bütçe). Boşsa sınırsız.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Onayı atla, doğrudan yaz."),
) -> None:
    """Bir markdown talimat dosyasını sadeleştirir, diff gösterir, onaylanırsa yazar."""
    source = read_source(str(file))

    editor = get_chat_model(editor_model)
    verifier = get_chat_model(verifier_model or editor_model)
    graph = build_graph(editor, verifier)

    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    initial_state = {
        "file_path": str(file),
        "encoding": source.encoding,
        "newline": source.newline,
        "raw_original_text": source.text,
        "max_attempts": max_attempts,
        "max_tokens": max_tokens,
    }

    console.print(f"[bold]Zarif[/bold] — {file} işleniyor...")
    result = graph.invoke(initial_state, config)

    report = token_report(source.text, result.get("final_text", source.text))
    console.print(f"Token: {report['before']} → {report['after']} ({report['reduction_pct']}% azalma)")

    if not result.get("diff_text", "").strip():
        console.print("[dim]Önerilecek bir değişiklik bulunamadı.[/dim]")
        return

    approved = yes or typer.confirm("Bu değişiklikleri dosyaya yazayım mı?", default=False)
    if not approved:
        console.print("[yellow]İptal edildi, dosya değiştirilmedi.[/yellow]")
        return

    final = graph.invoke(None, config)
    console.print(f"[green]Yazıldı.[/green] Yedek: {final.get('backup_path')}")


@app.command()
def restore(
    file: Path = typer.Argument(..., exists=True, readable=True, help="Yedeği geri yüklenecek dosya."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Onayı atla, doğrudan geri yükle."),
) -> None:
    """Bir dosyayı en son alınan `.bak` yedeğinden geri yükler."""
    backup_path = find_latest_backup(str(file))
    if backup_path is None:
        console.print(f"[red]Yedek bulunamadı: {file}.bak[/red]")
        raise typer.Exit(1)

    console.print(f"[bold]Zarif[/bold] — {backup_path} içeriği {file} üzerine geri yazılacak.")
    approved = yes or typer.confirm("Devam edeyim mi?", default=False)
    if not approved:
        console.print("[yellow]İptal edildi.[/yellow]")
        return

    restore_from_backup(backup_path, str(file))
    console.print(f"[green]Geri yüklendi.[/green] Kaynak yedek: {backup_path}")


@app.command()
def hierarchy(
    directory: Path = typer.Argument(
        Path("."), exists=True, file_okay=False, dir_okay=True, help="Taranacak kök dizin."
    ),
    as_json: bool = typer.Option(False, "--json", help="Sonucu JSON olarak yazdır."),
) -> None:
    """Bir dizin altındaki tüm talimat dosyalarını (CLAUDE.md, AGENTS.md, SKILL.md, ...) keşfeder.

    Her dosyanın token sayısını ve derinliğini raporlar, ardından dosyalar arası
    tekrar eden paragrafları bulur — daha sığ (genel) dosyada tutulup daha derin
    (özel) dosyadan çıkarılması önerilir. LLM/ağ gerektirmez, saf metin karşılaştırmasıdır.
    """
    root = directory.resolve()
    files = discover_instruction_files(root)
    if not files:
        console.print(f"[yellow]{root} altında talimat dosyası bulunamadı.[/yellow]")
        raise typer.Exit(0)

    duplicates = find_cross_file_duplicates(files)

    if as_json:
        payload = {
            "files": [{"path": str(f.path), "depth": f.depth, "tokens": f.tokens} for f in files],
            "duplicates": [
                {
                    "preview": d.paragraph_preview,
                    "keep_in": str(d.shallow_file),
                    "keep_in_depth": d.shallow_depth,
                    "remove_from": str(d.deep_file),
                    "remove_from_depth": d.deep_depth,
                    "similarity": d.similarity,
                }
                for d in duplicates
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    table = Table(title=f"Talimat dosyası hiyerarşisi — {root}")
    table.add_column("Dosya")
    table.add_column("Derinlik")
    table.add_column("Token")
    for f in files:
        table.add_row(str(f.path.relative_to(root)), str(f.depth), str(f.tokens))
    console.print(table)

    if not duplicates:
        console.print("[green]Dosyalar arası tekrar eden paragraf bulunamadı.[/green]")
        return

    console.print(f"\n[bold yellow]{len(duplicates)} tekrar eden paragraf bulundu:[/bold yellow]")
    for d in duplicates:
        console.print(
            f'  • "{d.paragraph_preview}" — [green]{d.shallow_file}[/green] (derinlik {d.shallow_depth}) '
            f"içinde tutulması, [red]{d.deep_file}[/red] (derinlik {d.deep_depth}) içinden çıkarılması "
            f"önerilir (benzerlik {d.similarity})"
        )


@app.command()
def audit(
    file: Path = typer.Argument(..., exists=True, readable=True, help="Denetlenecek markdown dosyası."),
    project_root: Path = typer.Option(
        Path("."), "--project-root", exists=True, file_okay=False, dir_okay=True,
        help="Ölü kural tespiti için konfigürasyon dosyalarının aranacağı kök dizin.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Sonucu JSON olarak yazdır."),
    sarif: Optional[Path] = typer.Option(
        None, "--sarif", help="Sonucu bu yola SARIF 2.1.0 dosyası olarak yaz (CI entegrasyonu için)."
    ),
) -> None:
    """Bir talimat dosyasını denetler: ölü kurallar, olası sır/PII sızıntısı ve çelişkiler.

    - Ölü kural: kural zaten .editorconfig/.prettierrc/tsconfig.json/pyproject.toml gibi
      bir konfigürasyon dosyası tarafından uygulanıyorsa işaretlenir.
    - Sır/PII taraması: AWS anahtarı, private key, Slack token, jenerik secret/password
      ataması gibi desenler regex ile aranır (yer tutucular hariç tutulur).
    - Çelişki tespiti (deneysel): zıt kutuplu (always/never) ve ortak kelime içeren satır
      çiftlerini sezgisel olarak işaretler; yanlış pozitif verebilir.

    Tümü deterministiktir — LLM veya ağ erişimi gerektirmez.
    """
    text = read_source(str(file)).text
    result = run_audit(text, project_root.resolve(), file.resolve().parent)

    if sarif is not None:
        sarif_doc = render_sarif(result, str(file))
        sarif.write_text(json.dumps(sarif_doc, ensure_ascii=False, indent=2), encoding="utf-8")
        console.print(f"[green]SARIF raporu yazıldı:[/green] {sarif}")

    if as_json:
        payload = {
            "dead_rules": [
                {"line": d.line_no, "rule": d.rule_snippet, "pattern": d.pattern_name, "reason": d.reason}
                for d in result.dead_rules
            ],
            "secrets": [
                {"line": s.line_no, "kind": s.kind, "redacted": s.redacted_snippet} for s in result.secrets
            ],
            "contradictions": [
                {
                    "line_a": c.line_a,
                    "text_a": c.text_a,
                    "line_b": c.line_b,
                    "text_b": c.text_b,
                    "shared_words": list(c.shared_words),
                }
                for c in result.contradictions
            ],
            "stale_references": [
                {"line": r.line_no, "reference": r.reference, "kind": r.kind, "reason": r.reason}
                for r in result.stale_references
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if not (result.dead_rules or result.secrets or result.contradictions or result.stale_references):
        console.print("[green]Sorun bulunamadı.[/green]")
        return

    if result.dead_rules:
        console.print(f"[bold yellow]Ölü kurallar ({len(result.dead_rules)}):[/bold yellow]")
        for d in result.dead_rules:
            console.print(f"  • satır {d.line_no}: \"{d.rule_snippet}\" — {d.reason}")

    if result.secrets:
        console.print(f"[bold red]Olası sır/PII ({len(result.secrets)}):[/bold red]")
        for s in result.secrets:
            console.print(f"  • satır {s.line_no}: {s.kind} — {s.redacted_snippet}")

    if result.contradictions:
        console.print(f"[bold magenta]Olası çelişkiler, deneysel ({len(result.contradictions)}):[/bold magenta]")
        for c in result.contradictions:
            console.print(f'  • satır {c.line_a} vs satır {c.line_b}: "{c.text_a}" ile "{c.text_b}"')

    if result.stale_references:
        console.print(f"[bold cyan]Bayat referanslar ({len(result.stale_references)}):[/bold cyan]")
        for r in result.stale_references:
            console.print(f"  • satır {r.line_no}: {r.reference} — {r.reason}", markup=False)


def _compute_benchmark_rows(
    files: list[Path],
    editor_model: str,
    verifier_model: Optional[str],
    max_attempts: int,
    downstream: bool,
    agent_model: Optional[str],
) -> list[tuple[str, dict, Optional[bool], Optional[tuple]]]:
    """Run parse→propose→verify for each sample file, optionally scoring downstream tasks."""
    editor = get_chat_model(editor_model)
    verifier = get_chat_model(verifier_model or editor_model)
    graph = build_graph(editor, verifier)
    agent = get_chat_model(agent_model or editor_model) if downstream else None

    rows = []
    for sample in files:
        source = read_source(str(sample))
        config = {"configurable": {"thread_id": str(uuid.uuid4())}}
        result = graph.invoke(
            {
                "file_path": str(sample),
                "encoding": source.encoding,
                "newline": source.newline,
                "raw_original_text": source.text,
                "max_attempts": max_attempts,
            },
            config,
        )
        report = token_report(source.text, result.get("final_text", source.text))
        final_text = result.get("final_text", source.text)

        task_scores = None
        if downstream and agent is not None:
            task = get_task_for(sample)
            if task is not None:
                raw_result = run_downstream(agent, source.text, task)
                edited_result = run_downstream(agent, final_text, task)
                task_scores = (raw_result, edited_result)

        rows.append((sample.name, report, result.get("sufficient"), task_scores))
    return rows


@app.command()
def benchmark(
    directory: Optional[Path] = typer.Option(
        None, "--dir", help="Örnek dosyaların bulunduğu klasör (varsayılan: gömülü örnekler)."
    ),
    editor_model: str = typer.Option("ollama:llama3.1", "--editor-model", help=_EDITOR_MODEL_HELP),
    verifier_model: Optional[str] = typer.Option(None, "--verifier-model"),
    max_attempts: int = typer.Option(2, "--max-attempts", min=1),
    downstream: bool = typer.Option(
        False, "--downstream/--no-downstream", help="Kayıtlı görev seti varsa aşağı akış performansını da ölç (ekstra model çağrısı gerektirir)."
    ),
    agent_model: Optional[str] = typer.Option(
        None, "--agent-model", help="Aşağı akış görevinde 'agent' olarak kullanılacak model. Boşsa editor-model kullanılır."
    ),
    as_json: bool = typer.Option(
        False, "--json", help="Sonucu insan-okunur tablo yerine makine-okunur JSON olarak yazdır."
    ),
) -> None:
    """Örnek dosyalar üzerinde token azalması ve bilgi korunumunu ölçer, hiçbir dosyaya yazmaz."""
    base = directory or _SAMPLES_DIR
    files = sorted(base.glob("*.md"))
    if not files:
        console.print(f"[red]Örnek dosya bulunamadı: {base}[/red]")
        raise typer.Exit(1)

    rows = _compute_benchmark_rows(files, editor_model, verifier_model, max_attempts, downstream, agent_model)

    if as_json:
        payload = []
        for name, report, sufficient, task_scores in rows:
            entry: dict = {"name": name, "tokens": report, "sufficient": bool(sufficient)}
            if task_scores is not None:
                raw_result, edited_result = task_scores
                entry["downstream"] = {
                    "raw": {"qa_score": list(raw_result.qa_score), "scenario_score": list(raw_result.scenario_score)},
                    "edited": {
                        "qa_score": list(edited_result.qa_score),
                        "scenario_score": list(edited_result.scenario_score),
                    },
                }
            payload.append(entry)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    console.print("\n[bold]Benchmark sonucu (dosyalara yazma yapılmadı)[/bold]")
    for name, report, sufficient, task_scores in rows:
        status = "✅" if sufficient else "⚠️"
        console.print(f"{status} {name}: {report['before']} → {report['after']} token ({report['reduction_pct']}%)")
        if task_scores is not None:
            raw_result, edited_result = task_scores
            raw_qa, raw_qa_total = raw_result.qa_score
            edited_qa, edited_qa_total = edited_result.qa_score
            raw_sc, raw_sc_total = raw_result.scenario_score
            edited_sc, edited_sc_total = edited_result.scenario_score
            console.print(
                f"   aşağı akış — soru-cevap: ham {raw_qa}/{raw_qa_total} → düzenlenmiş {edited_qa}/{edited_qa_total} | "
                f"senaryo: ham {raw_sc}/{raw_sc_total} → düzenlenmiş {edited_sc}/{edited_sc_total}"
            )
        elif downstream:
            console.print("   [dim]aşağı akış görevi bu dosya için tanımlı değil.[/dim]")


@app.command()
def dashboard(
    directory: Optional[Path] = typer.Option(
        None, "--dir", help="Örnek dosyaların bulunduğu klasör (varsayılan: gömülü örnekler)."
    ),
    editor_model: str = typer.Option("ollama:llama3.1", "--editor-model", help=_EDITOR_MODEL_HELP),
    verifier_model: Optional[str] = typer.Option(None, "--verifier-model"),
    max_attempts: int = typer.Option(2, "--max-attempts", min=1),
    agent_model: Optional[str] = typer.Option(
        None, "--agent-model", help="Aşağı akış görevinde 'agent' olarak kullanılacak model. Boşsa editor-model kullanılır."
    ),
    out: Path = typer.Option(Path("dashboard.html"), "--out", help="HTML panosunun yazılacağı dosya."),
) -> None:
    """Token azalması, bilgi korunumu ve aşağı akış ölçütlerinin tümünü tek bir HTML panosunda birleştirir.

    `zarif benchmark --downstream`'in yaptığı her şeyi çalıştırır (aşağı akış
    dahil olduğu için ekstra model çağrıları gerektirir), sonra hem terminalde
    özet bir tablo gösterir hem de paylaşılabilir bir `--out` HTML dosyası yazar.
    """
    base = directory or _SAMPLES_DIR
    files = sorted(base.glob("*.md"))
    if not files:
        console.print(f"[red]Örnek dosya bulunamadı: {base}[/red]")
        raise typer.Exit(1)

    console.print("[bold]Zarif[/bold] — pano için tüm ölçütler hesaplanıyor...")
    rows = _compute_benchmark_rows(files, editor_model, verifier_model, max_attempts, True, agent_model)

    dashboard_rows = [
        BenchmarkRow(
            name=name,
            tokens_before=report["before"],
            tokens_after=report["after"],
            reduction_pct=report["reduction_pct"],
            sufficient=sufficient,
            raw_downstream=task_scores[0] if task_scores else None,
            edited_downstream=task_scores[1] if task_scores else None,
        )
        for name, report, sufficient, task_scores in rows
    ]

    table = Table(title="Zarif Benchmark Panosu")
    table.add_column("Dosya")
    table.add_column("Korunum")
    table.add_column("Token (öncesi → sonrası)")
    table.add_column("Azalma")
    table.add_column("QA (ham → düzenlenmiş)")
    table.add_column("Senaryo (ham → düzenlenmiş)")
    for row in dashboard_rows:
        status = "✅" if row.sufficient else ("⚠️" if row.sufficient is not None else "—")
        raw_qa = format_score(row.raw_downstream.qa_score) if row.raw_downstream else "—"
        edited_qa = format_score(row.edited_downstream.qa_score) if row.edited_downstream else "—"
        raw_sc = format_score(row.raw_downstream.scenario_score) if row.raw_downstream else "—"
        edited_sc = format_score(row.edited_downstream.scenario_score) if row.edited_downstream else "—"
        table.add_row(
            row.name,
            status,
            f"{row.tokens_before} → {row.tokens_after}",
            f"{row.reduction_pct}%",
            f"{raw_qa} → {edited_qa}",
            f"{raw_sc} → {edited_sc}",
        )
    console.print(table)

    out.write_text(render_html(dashboard_rows), encoding="utf-8")
    console.print(f"[green]HTML pano yazıldı:[/green] {out}")


@app.command()
def score(
    target: Path = typer.Argument(
        Path("."), exists=True,
        help="Skorlanacak talimat dosyası veya dizin (varsayılan: mevcut dizin).",
    ),
    project_root: Optional[Path] = typer.Option(
        None, "--project-root", exists=True, file_okay=False, dir_okay=True,
        help="Audit/suggest için konfigürasyon dosyalarının aranacağı kök (varsayılan: target'ın üst dizini).",
    ),
    fail_under: Optional[int] = typer.Option(
        None, "--fail-under", min=0, max=100,
        help="Skor bu değerin altındaysa exit code 1 döner (CI gate için).",
    ),
    as_json: bool = typer.Option(False, "--json", help="Sonucu JSON olarak yazdır."),
) -> None:
    """Bir talimat dosyası veya dizin için 0-100 sağlık skoru hesaplar.

    Mevcut tüm deterministik kontrolleri birleştirir: token sayısı, ölü
    kurallar, secret/PII, çelişkiler, eksik operasyonel bilgi ve (dizin
    modunda) tekrar eden paragraflar. LLM/ağ gerektirmez.

    CI'da `--fail-under 80` ile skor threshold'una göre fail edebilir.
    """
    from .score import render_score, score_directory, score_file

    if target.is_dir():
        report = score_directory(target)
    else:
        text = read_source(str(target)).text
        root = (project_root or target.parent).resolve()
        report = score_file(text, root, str(target))

    if as_json:
        payload = {
            "target": report.target,
            "score": report.score,
            "grade": report.grade,
            "token_count": report.token_count,
            "is_directory": report.is_directory,
            "categories": [
                {
                    "name": c.name,
                    "label": c.label,
                    "findings": c.findings,
                    "points_deducted": c.points_deducted,
                    "max_deduction": c.max_deduction,
                    "details": c.details,
                }
                for c in report.categories
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        from rich.markdown import Markdown

        console.print(f"[bold]Zarif score[/bold] — {report.target}\n")
        console.print(Markdown(render_score(report)))

    if fail_under is not None and report.score < fail_under:
        console.print(f"\n[red]Skor {report.score} < {fail_under} — FAIL[/red]")
        raise typer.Exit(1)


@app.command()
def check(
    paths: list[Path] = typer.Argument(
        None, help="Kontrol edilecek talimat dosyaları veya dizinler (varsayılan: mevcut dizin)."
    ),
    project_root: Path = typer.Option(
        Path("."), "--project-root", exists=True, file_okay=False, dir_okay=True,
        help="Konfigürasyon ve referansların doğrulanacağı proje kökü.",
    ),
    fail_under: Optional[int] = typer.Option(
        None, "--fail-under", min=0, max=100,
        help="Herhangi bir dosyanın skoru bu değerin altındaysa exit code 1 döner.",
    ),
    sarif: Optional[Path] = typer.Option(
        None, "--sarif", help="Tüm dosyaların bulgularını tek SARIF 2.1.0 dosyası olarak yaz."
    ),
    as_json: bool = typer.Option(False, "--json", help="Sonucu JSON olarak yazdır."),
) -> None:
    """CI/pre-commit kapısı: audit + score'u çoklu dosya üzerinde çalıştırır.

    Exit code 1: herhangi bir dosyada olası secret/PII bulunursa (her zaman) ya da
    `--fail-under` verilmiş ve bir dosyanın skoru eşiğin altındaysa. Diğer bulgular
    (ölü kural, bayat referans, çelişki) raporlanır ama tek başına fail ettirmez.
    Dizin verilirse bilinen talimat dosyaları (CLAUDE.md, AGENTS.md, ...) otomatik bulunur.
    """
    from .score import score_file

    root = project_root.resolve()
    files: list[Path] = []
    for p in paths or [Path(".")]:
        if not p.exists():
            console.print(f"[red]Bulunamadı: {p}[/red]")
            raise typer.Exit(2)
        if p.is_dir():
            files.extend(f.path for f in discover_instruction_files(p))
        else:
            files.append(p.resolve())
    files = list(dict.fromkeys(files))

    rows: list[dict] = []
    sarif_docs: list[dict] = []
    failed = False
    for f in files:
        text = read_source(str(f)).text
        result = run_audit(text, root, f.parent)
        try:
            display = str(f.relative_to(root))
        except ValueError:
            display = str(f)
        report = score_file(text, root, display)
        below = fail_under is not None and report.score < fail_under
        failed = failed or bool(result.secrets) or below
        sarif_docs.append(render_sarif(result, display.replace("\\", "/")))
        rows.append(
            {
                "file": display,
                "score": report.score,
                "grade": report.grade,
                "secrets": len(result.secrets),
                "dead_rules": len(result.dead_rules),
                "stale_references": len(result.stale_references),
                "contradictions": len(result.contradictions),
                "failed": bool(result.secrets) or below,
            }
        )

    if sarif is not None:
        sarif.write_text(json.dumps(merge_sarif(sarif_docs), ensure_ascii=False, indent=2), encoding="utf-8")

    if as_json:
        print(json.dumps({"passed": not failed, "files": rows}, ensure_ascii=False, indent=2))
    elif not rows:
        console.print("Kontrol edilecek talimat dosyası bulunamadı.")
    else:
        table = Table(show_header=True, header_style="bold")
        for col in ("Dosya", "Skor", "Secret", "Ölü kural", "Bayat ref.", "Çelişki", "Durum"):
            table.add_column(col)
        for r in rows:
            table.add_row(
                r["file"], f"{r['score']} ({r['grade']})", str(r["secrets"]), str(r["dead_rules"]),
                str(r["stale_references"]), str(r["contradictions"]),
                "[red]FAIL[/red]" if r["failed"] else "[green]OK[/green]",
            )
        console.print(table)

    if failed:
        raise typer.Exit(1)


@app.command()
def suggest(
    file: Path = typer.Argument(..., exists=True, readable=True, help="Öneri üretilecek talimat dosyası."),
    project_root: Path = typer.Option(
        Path("."), "--project-root", exists=True, file_okay=False, dir_okay=True,
        help="Kaynak kodun aranacağı kök dizin (varsayılan: mevcut dizin).",
    ),
    as_json: bool = typer.Option(False, "--json", help="Sonucu JSON olarak yazdır."),
) -> None:
    """Talimat dosyasında eksik operasyonel bilgileri tespit eder (öneri modu).

    Kaynak koddan (Makefile, package.json, docker-compose.yml, .env.example,
    Dockerfile, pyproject.toml vb.) talimat dosyasında geçmeyen ortam
    değişkenlerini, build komutlarını, servisleri, portları ve konfigürasyon
    dosyalarını deterministik olarak bulur.

    **Dosyaya asla yazmaz** — Prensip 1'e uygun "öneri modu". Uygun gördüğünüz
    önerileri elle kopyalayıp talimat dosyanıza ekleyin. LLM/ağ gerektirmez.
    """
    from .suggest import render_suggestions, run_suggestions

    text = read_source(str(file)).text
    report = run_suggestions(text, project_root.resolve())
    report.instruction_file = str(file)

    if as_json:
        payload = {
            "file": str(file),
            "suggestions": [
                {
                    "category": s.category,
                    "title": s.title,
                    "detail": s.detail,
                    "source_file": s.source_file,
                    "suggested_text": s.suggested_text,
                }
                for s in report.suggestions
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if not report.has_suggestions:
        console.print("[green]Eksik operasyonel bilgi bulunamadı — talimat dosyası kapsamlı görünüyor.[/green]")
        return

    console.print(f"[bold]Zarif suggest[/bold] — {file}\n")
    # Markdown çıktısını rich ile render et.
    from rich.markdown import Markdown

    console.print(Markdown(render_suggestions(report)))


@app.command()
def batch(
    directory: Path = typer.Argument(
        Path("."), exists=True, file_okay=False, dir_okay=True,
        help="Taranacak kök dizin (varsayılan: mevcut dizin).",
    ),
    editor_model: str = typer.Option("ollama:llama3.1", "--editor-model", help=_EDITOR_MODEL_HELP),
    verifier_model: Optional[str] = typer.Option(
        None, "--verifier-model", help="Boş bırakılırsa editor-model ile aynısı kullanılır."
    ),
    max_attempts: int = typer.Option(2, "--max-attempts", min=1),
    max_tokens: Optional[int] = typer.Option(
        None, "--max-tokens", min=1,
        help="Düzenlenmiş metnin en fazla kaç token olacağı (bütçe). Boşsa sınırsız.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Onayı atla, tüm dosyalara doğrudan yaz."),
    as_json: bool = typer.Option(False, "--json", help="Sonucu JSON olarak yazdır."),
) -> None:
    """Bir dizin altındaki tüm talimat dosyalarını toplu sadeleştirir.

    `zarif hierarchy` ile aynı keşif mantığını kullanır (CLAUDE.md, AGENTS.md,
    SKILL.md, .cursorrules, *copilot-instructions.md). Her dosya için pipeline
    çalıştırılır, özet tablo gösterilir; onay sonrası tüm dosyalara yedek
    alarak yazılır.
    """
    from .api import edit_file

    root = directory.resolve()
    files = discover_instruction_files(root)
    if not files:
        console.print(f"[yellow]{root} altında talimat dosyası bulunamadı.[/yellow]")
        raise typer.Exit(0)

    console.print(f"[bold]Zarif batch[/bold] — {len(files)} dosya işleniyor...\n")

    results = []
    for f in files:
        rel = f.path.relative_to(root)
        console.print(f"  • {rel} ... ", end="")
        try:
            result = edit_file(str(f.path), editor_model=editor_model, verifier_model=verifier_model, max_attempts=max_attempts, max_tokens=max_tokens)
            has_change = bool(result.diff_text.strip())
            status = "✅" if (has_change and result.sufficient) else ("⚠️" if has_change else "—")
            console.print(f"{status} {result.token_report['before']} → {result.token_report['after']} ({result.token_report['reduction_pct']}%)")
            results.append((f.path, result))
        except Exception as e:
            console.print(f"[red]hata: {e}[/red]")
            results.append((f.path, None))

    # Özet tablo
    table = Table(title="Zarif Batch Özeti")
    table.add_column("Dosya")
    table.add_column("Token (öncesi → sonrası)")
    table.add_column("Azalma")
    table.add_column("Korunum")
    table.add_column("Değişiklik")
    for path, result in results:
        rel = str(path.relative_to(root))
        if result is None:
            table.add_row(rel, "—", "—", "—", "[red]hata[/red]")
        elif not result.diff_text.strip():
            table.add_row(rel, f"{result.token_report['before']} → {result.token_report['after']}", "0%", "—", "[dim]yok[/dim]")
        else:
            status = "✅" if result.sufficient else "⚠️"
            table.add_row(
                rel,
                f"{result.token_report['before']} → {result.token_report['after']}",
                f"{result.token_report['reduction_pct']}%",
                status,
                "var",
            )
    console.print(table)

    # JSON çıktı
    if as_json:
        payload = []
        for path, result in results:
            entry: dict = {"file": str(path.relative_to(root))}
            if result is not None:
                entry["tokens"] = result.token_report
                entry["sufficient"] = result.sufficient
                entry["has_changes"] = bool(result.diff_text.strip())
            else:
                entry["error"] = True
            payload.append(entry)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    # Uygulanacak dosyalar
    to_apply = [(p, r) for p, r in results if r is not None and r.diff_text.strip()]
    if not to_apply:
        console.print("[green]Uygulanacak değişiklik yok.[/green]")
        for _, r in results:
            if r is not None:
                r.discard()
        return

    console.print(f"\n[bold]{len(to_apply)} dosyaya yazılacak.[/bold]")
    approved = yes or typer.confirm("Devam edeyim mi?", default=False)
    if not approved:
        console.print("[yellow]İptal edildi, hiçbir dosya değiştirilmedi.[/yellow]")
        for _, r in results:
            if r is not None:
                r.discard()
        return

    for path, result in to_apply:
        try:
            backup = result.apply()
            console.print(f"  [green]✓[/green] {path.relative_to(root)} (yedek: {Path(backup).name})")
        except Exception as e:
            rel_err = path.relative_to(root)
            console.print(f"  [red]✗ {rel_err}: {e}[/red]")

    for _, r in results:
        if r is not None and not r._applied:
            r.discard()

    console.print(f"\n[green]Tamamlandı: {len(to_apply)} dosya güncellendi.[/green]")


@app.command()
def serve() -> None:
    """Zarif'i bir MCP sunucusu olarak çalıştırır (stdio).

    Bu modda editör/doğrulayıcı model çağrıları, bağlı MCP host'un kendi
    modeline (sampling) devredilir — ayrı bir --editor-model kurulumu
    gerekmez. Bkz. README 'MCP sunucusu' bölümü.
    """
    from .mcp_server import run_server

    run_server()


def main() -> None:
    app()


if __name__ == "__main__":
    main()
