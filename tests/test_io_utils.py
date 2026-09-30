from pathlib import Path

from zarif.io_utils import (
    detect_encoding,
    detect_newline,
    find_latest_backup,
    make_backup,
    protect_blocks,
    read_source,
    restore_blocks,
    restore_from_backup,
    write_target,
)


def test_detect_encoding_utf8_sig():
    assert detect_encoding(b"\xef\xbb\xbfhello") == "utf-8-sig"


def test_detect_encoding_plain_utf8():
    assert detect_encoding(b"hello") == "utf-8"


def test_detect_newline_crlf():
    assert detect_newline("a\r\nb\r\nc") == "\r\n"


def test_detect_newline_lf():
    assert detect_newline("a\nb\nc") == "\n"


def test_protect_and_restore_code_block_roundtrip():
    text = "Before\n\n```python\nprint('hi')\n```\n\nAfter"
    protected, mapping = protect_blocks(text)
    assert "```" not in protected
    assert restore_blocks(protected, mapping) == text


def test_protect_and_restore_table_roundtrip():
    text = "Intro\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\nOutro"
    protected, mapping = protect_blocks(text)
    assert "| a | b |" not in protected
    assert restore_blocks(protected, mapping) == text


def test_read_source_and_write_target_roundtrip(tmp_path: Path):
    original = "Hello\r\nWörld\r\n"
    path = tmp_path / "doc.md"
    path.write_bytes(original.encode("utf-8-sig"))

    source = read_source(str(path))
    assert source.encoding == "utf-8-sig"
    assert source.newline == "\r\n"
    assert source.text == "Hello\nWörld\n"

    write_target(str(path), source.text, source.encoding, source.newline)
    assert path.read_bytes() == original.encode("utf-8-sig")


def test_make_backup_creates_copy(tmp_path: Path):
    path = tmp_path / "doc.md"
    path.write_text("content", encoding="utf-8")

    backup_path = make_backup(str(path))

    assert Path(backup_path).read_text(encoding="utf-8") == "content"
    assert backup_path != str(path)


def test_find_latest_backup_returns_none_when_missing(tmp_path: Path):
    path = tmp_path / "doc.md"
    path.write_text("content", encoding="utf-8")

    assert find_latest_backup(str(path)) is None


def test_find_latest_backup_returns_highest_numbered_suffix(tmp_path: Path):
    path = tmp_path / "doc.md"
    path.write_text("v3", encoding="utf-8")

    first = make_backup(str(path))  # doc.md.bak
    second = make_backup(str(path))  # doc.md.bak1
    third = make_backup(str(path))  # doc.md.bak2

    assert find_latest_backup(str(path)) == third
    assert {first, second, third} == {
        str(path) + ".bak",
        str(path) + ".bak1",
        str(path) + ".bak2",
    }


def test_restore_from_backup_overwrites_target(tmp_path: Path):
    path = tmp_path / "doc.md"
    path.write_text("original", encoding="utf-8")
    backup_path = make_backup(str(path))

    path.write_text("changed", encoding="utf-8")
    restore_from_backup(backup_path, str(path))

    assert path.read_text(encoding="utf-8") == "original"
