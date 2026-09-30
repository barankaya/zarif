"""Pure/deterministic tests for the HTML dashboard renderer (no model calls)."""

from __future__ import annotations

from zarif.benchmark.dashboard import BenchmarkRow, format_score, render_html
from zarif.benchmark.downstream import DownstreamResult, QAResult, ScenarioResult


def _downstream_result(qa_correct: int, qa_total: int, sc_passed: int, sc_total: int) -> DownstreamResult:
    qa_results = [QAResult(question=f"q{i}", answer="a", correct=i < qa_correct) for i in range(qa_total)]
    scenario_results = [ScenarioResult(name=f"s{i}", response="r", passed=i < sc_passed) for i in range(sc_total)]
    return DownstreamResult(qa_results=qa_results, scenario_results=scenario_results)


def test_format_score_none_returns_placeholder():
    assert format_score(None) == "—"


def test_format_score_formats_fraction():
    assert format_score((2, 3)) == "2/3"


def test_render_html_includes_file_name_and_metrics():
    rows = [
        BenchmarkRow(
            name="agent-instructions-1.md",
            tokens_before=500,
            tokens_after=300,
            reduction_pct=40.0,
            sufficient=True,
            raw_downstream=_downstream_result(1, 3, 1, 2),
            edited_downstream=_downstream_result(3, 3, 2, 2),
        )
    ]

    html = render_html(rows)

    assert "agent-instructions-1.md" in html
    assert "40.0%" in html
    assert "500 → 300" in html
    assert "1/3 → 3/3" in html
    assert "1/2 → 2/2" in html
    assert "<html" in html and "</html>" in html


def test_render_html_escapes_file_names():
    rows = [
        BenchmarkRow(
            name="<script>alert(1)</script>.md",
            tokens_before=10,
            tokens_after=10,
            reduction_pct=0.0,
            sufficient=None,
        )
    ]

    html = render_html(rows)

    assert "<script>alert(1)</script>.md" not in html
    assert "&lt;script&gt;" in html


def test_render_html_handles_empty_rows():
    html = render_html([])

    assert "Sonuç yok" in html
    assert "0/0" in html
