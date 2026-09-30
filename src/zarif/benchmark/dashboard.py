"""HTML dashboard export combining all benchmark metrics (token, retention, downstream).

Pure/deterministic rendering only — no model calls happen here. The CLI's
`dashboard` command computes the actual metrics (reusing the same LangGraph
pipeline as `benchmark`) and passes the results in as `BenchmarkRow`s.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from typing import Optional

from .downstream import DownstreamResult


@dataclass
class BenchmarkRow:
    name: str
    tokens_before: int
    tokens_after: int
    reduction_pct: float
    sufficient: Optional[bool]
    raw_downstream: Optional[DownstreamResult] = None
    edited_downstream: Optional[DownstreamResult] = None


def format_score(score: Optional[tuple[int, int]]) -> str:
    if score is None:
        return "—"
    correct, total = score
    return f"{correct}/{total}"


def render_html(rows: list[BenchmarkRow]) -> str:
    """Render a single self-contained HTML report (no external assets/CDN deps)."""
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    total_before = sum(r.tokens_before for r in rows)
    total_after = sum(r.tokens_after for r in rows)
    avg_reduction = round(sum(r.reduction_pct for r in rows) / len(rows), 1) if rows else 0.0
    sufficient_count = sum(1 for r in rows if r.sufficient)

    body_rows = []
    for r in rows:
        status = "✅" if r.sufficient else ("⚠️" if r.sufficient is not None else "—")
        raw_qa = format_score(r.raw_downstream.qa_score) if r.raw_downstream else "—"
        edited_qa = format_score(r.edited_downstream.qa_score) if r.edited_downstream else "—"
        raw_sc = format_score(r.raw_downstream.scenario_score) if r.raw_downstream else "—"
        edited_sc = format_score(r.edited_downstream.scenario_score) if r.edited_downstream else "—"
        body_rows.append(
            "<tr>"
            f"<td>{escape(r.name)}</td>"
            f"<td>{status}</td>"
            f"<td>{r.tokens_before} → {r.tokens_after}</td>"
            f"<td>{r.reduction_pct}%</td>"
            f"<td>{escape(raw_qa)} → {escape(edited_qa)}</td>"
            f"<td>{escape(raw_sc)} → {escape(edited_sc)}</td>"
            "</tr>"
        )
    table_body = "\n      ".join(body_rows) if body_rows else '<tr><td colspan="6">Sonuç yok</td></tr>'

    return f"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="utf-8">
<title>Zarif Benchmark Panosu</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 2rem; background: #0f172a; color: #e2e8f0; }}
  h1 {{ margin-bottom: 0.2rem; }}
  .meta {{ color: #94a3b8; margin-bottom: 1.5rem; }}
  .summary {{ display: flex; gap: 1rem; margin-bottom: 2rem; flex-wrap: wrap; }}
  .card {{ background: #1e293b; border-radius: 8px; padding: 1rem 1.5rem; min-width: 160px; }}
  .card .value {{ font-size: 1.6rem; font-weight: 700; }}
  .card .label {{ color: #94a3b8; font-size: 0.85rem; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ text-align: left; padding: 0.5rem 0.75rem; border-bottom: 1px solid #334155; }}
  th {{ color: #94a3b8; font-weight: 600; }}
</style>
</head>
<body>
  <h1>Zarif Benchmark Panosu</h1>
  <div class="meta">Oluşturulma: {generated_at}</div>
  <div class="summary">
    <div class="card"><div class="value">{len(rows)}</div><div class="label">Dosya</div></div>
    <div class="card"><div class="value">{total_before} → {total_after}</div><div class="label">Toplam token</div></div>
    <div class="card"><div class="value">{avg_reduction}%</div><div class="label">Ortalama azalma</div></div>
    <div class="card"><div class="value">{sufficient_count}/{len(rows)}</div><div class="label">Bilgi korunumu yeterli</div></div>
  </div>
  <table>
    <thead>
      <tr><th>Dosya</th><th>Korunum</th><th>Token (öncesi → sonrası)</th><th>Azalma</th><th>QA (ham → düzenlenmiş)</th><th>Senaryo (ham → düzenlenmiş)</th></tr>
    </thead>
    <tbody>
      {table_body}
    </tbody>
  </table>
</body>
</html>
"""
