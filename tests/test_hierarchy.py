from pathlib import Path

from zarif.hierarchy import discover_instruction_files, find_cross_file_duplicates

REPEATED_PARAGRAPH = (
    "Always run the test suite before committing any change and make sure "
    "the coverage report has no regressions before opening a pull request."
)


def test_discover_instruction_files_finds_known_names_and_depth(tmp_path: Path):
    (tmp_path / "CLAUDE.md").write_text("root rules", encoding="utf-8")
    sub = tmp_path / "api"
    sub.mkdir()
    (sub / "AGENTS.md").write_text("api rules", encoding="utf-8")
    (tmp_path / "README.md").write_text("not an instruction file", encoding="utf-8")

    files = discover_instruction_files(tmp_path)

    names_and_depths = {(f.path.name, f.depth) for f in files}
    assert ("CLAUDE.md", 0) in names_and_depths
    assert ("AGENTS.md", 1) in names_and_depths
    assert all(f.path.name != "README.md" for f in files)


def test_discover_instruction_files_skips_vendor_dirs(tmp_path: Path):
    vendor = tmp_path / "node_modules" / "pkg"
    vendor.mkdir(parents=True)
    (vendor / "AGENTS.md").write_text("should be ignored", encoding="utf-8")

    files = discover_instruction_files(tmp_path)

    assert files == []


def test_discover_instruction_files_computes_tokens(tmp_path: Path):
    (tmp_path / "CLAUDE.md").write_text("one two three four five", encoding="utf-8")

    files = discover_instruction_files(tmp_path)

    assert files[0].tokens > 0


def test_find_cross_file_duplicates_flags_repeated_paragraph_and_recommends_shallower_file(
    tmp_path: Path,
):
    (tmp_path / "CLAUDE.md").write_text(f"# Global\n\n{REPEATED_PARAGRAPH}\n", encoding="utf-8")
    sub = tmp_path / "repo-a"
    sub.mkdir()
    (sub / "CLAUDE.md").write_text(f"# Repo A\n\n{REPEATED_PARAGRAPH}\n\nRepo-specific note.", encoding="utf-8")

    files = discover_instruction_files(tmp_path)
    duplicates = find_cross_file_duplicates(files)

    assert len(duplicates) == 1
    finding = duplicates[0]
    assert finding.shallow_depth == 0
    assert finding.deep_depth == 1
    assert finding.similarity >= 0.9


def test_find_cross_file_duplicates_ignores_short_paragraphs(tmp_path: Path):
    (tmp_path / "CLAUDE.md").write_text("# Title\n\nShort.", encoding="utf-8")
    sub = tmp_path / "repo-a"
    sub.mkdir()
    (sub / "CLAUDE.md").write_text("# Title\n\nShort.", encoding="utf-8")

    files = discover_instruction_files(tmp_path)
    duplicates = find_cross_file_duplicates(files)

    assert duplicates == []


def test_find_cross_file_duplicates_empty_for_unique_content(tmp_path: Path):
    (tmp_path / "CLAUDE.md").write_text(
        "This is a fairly long and completely unique paragraph about the root project.",
        encoding="utf-8",
    )
    sub = tmp_path / "repo-a"
    sub.mkdir()
    (sub / "CLAUDE.md").write_text(
        "This is a totally different and equally long paragraph about repo A specifics.",
        encoding="utf-8",
    )

    files = discover_instruction_files(tmp_path)
    duplicates = find_cross_file_duplicates(files)

    assert duplicates == []
