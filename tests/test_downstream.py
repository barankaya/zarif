from pathlib import Path

from zarif.benchmark.downstream import TASKS, DownstreamResult, QAResult, ScenarioResult, _contains_any, get_task_for


def test_contains_any_case_insensitive():
    assert _contains_any("Please Escalate this to a Human Agent.", ["escalate"])
    assert not _contains_any("Everything is fine.", ["escalate", "refund"])


def test_get_task_for_known_sample():
    task = get_task_for(Path("agent-instructions-1.md"))
    assert task is not None
    assert task.qa_items
    assert task.scenarios


def test_get_task_for_unknown_sample():
    assert get_task_for(Path("does-not-exist.md")) is None


def test_all_registered_tasks_have_items():
    for name, task in TASKS.items():
        assert task.qa_items, f"{name} için soru-cevap seti boş olmamalı"
        assert task.scenarios, f"{name} için senaryo seti boş olmamalı"


def test_downstream_result_scores():
    result = DownstreamResult(
        qa_results=[QAResult(question="q", answer="a", correct=True), QAResult(question="q2", answer="b", correct=False)],
        scenario_results=[ScenarioResult(name="s", response="r", passed=True)],
    )
    assert result.qa_score == (1, 2)
    assert result.scenario_score == (1, 1)
