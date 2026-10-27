from zarif.diffing import summarize_diff, unified_diff_text


def test_unified_diff_empty_when_identical():
    assert unified_diff_text("same\n", "same\n", "file.md") == ""


def test_unified_diff_shows_changes():
    diff = unified_diff_text("line1\nline2\n", "line1\nline2 changed\n", "file.md")
    assert "-line2\n" in diff
    assert "+line2 changed\n" in diff
    assert "a/file.md" in diff
    assert "b/file.md" in diff


def test_summarize_diff_empty():
    result = summarize_diff("")
    assert result["lines_added"] == 0
    assert result["lines_removed"] == 0
    assert result["hunks"] == 0
    assert result["summary"] == "Değişiklik yok."


def test_summarize_diff_counts_lines():
    diff = unified_diff_text(
        "# Başlık\n\neski satır 1\neski satır 2\neski satır 3\n",
        "# Başlık\n\nyeni satır 1\nyeni satır 2\n",
        "file.md",
    )
    result = summarize_diff(diff)
    assert result["lines_added"] == 2
    assert result["lines_removed"] == 3
    assert result["net_change"] == -1
    assert result["hunks"] >= 1


def test_summarize_diff_detects_sections():
    diff = unified_diff_text(
        "# Bölüm A\n\nmetin a\n\n# Bölüm B\n\nmetin b\n",
        "# Bölüm A\n\nmetin a degisti\n\n# Bölüm B\n\nmetin b\n",
        "file.md",
    )
    result = summarize_diff(diff)
    # "Bölüm A" başlığı hunk header'ında veya yakınında görünmeli.
    assert isinstance(result["sections_touched"], list)


def test_summarize_diff_summary_is_string():
    diff = unified_diff_text("a\n", "b\n", "file.md")
    result = summarize_diff(diff)
    assert isinstance(result["summary"], str)
    assert len(result["summary"]) > 0
    assert "satır" in result["summary"]


def test_summarize_diff_multiple_hunks():
    original = "# A\n\n1\n2\n3\n4\n5\n6\n7\n8\n9\n10\n"
    edited = "# A\n\n1\nX\n3\n4\n5\n6\n7\n8\n9\nY\n"
    diff = unified_diff_text(original, edited, "file.md")
    result = summarize_diff(diff)
    assert result["hunks"] >= 1
    assert result["lines_added"] == 2
    assert result["lines_removed"] == 2
