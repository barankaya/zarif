from zarif.diffing import unified_diff_text


def test_unified_diff_empty_when_identical():
    assert unified_diff_text("same\n", "same\n", "file.md") == ""


def test_unified_diff_shows_changes():
    diff = unified_diff_text("line1\nline2\n", "line1\nline2 changed\n", "file.md")
    assert "-line2\n" in diff
    assert "+line2 changed\n" in diff
    assert "a/file.md" in diff
    assert "b/file.md" in diff
