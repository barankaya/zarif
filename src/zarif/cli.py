"""`zarif edit`, `zarif restore` and `zarif benchmark` CLI commands."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .audit import render_sarif, run_audit
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
    result = run_audit(text, project_root.resolve())

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
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if not (result.dead_rules or result.secrets or result.contradictions):
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
