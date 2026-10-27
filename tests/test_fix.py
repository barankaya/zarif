from pathlib import Path

from typer.testing import CliRunner

from zarif.audit import redact_secrets, scan_for_secrets
from zarif.cli import app
from zarif.fix import apply_safe_fixes

runner = CliRunner()

_EDITORCONFIG = "[*]\nindent_style = space\nindent_size = 2\n"


def test_redact_secrets_masks_values_keeps_line_structure_and_is_idempotent():
    text = 'key: AKIAQ7K3M9X2B5N8R1T4\napi_key = "Zx9Qw8Er7Ty6Ui5"\nkeep me\n'

    redacted, findings = redact_secrets(text)

    assert redacted == 'key: <REDACTED>\napi_key = "<REDACTED>"\nkeep me\n'
    assert [f.line_no for f in findings] == [1, 2]
    assert scan_for_secrets(redacted) == []
    assert redact_secrets(redacted)[1] == []


def test_redact_secrets_leaves_private_key_header_and_internal_ip_alone():
    text = "-----BEGIN RSA PRIVATE KEY-----\nhost 10.1.2.3\n"

    redacted, findings = redact_secrets(text)

    assert redacted == text
    assert findings == []


def test_apply_safe_fixes_removes_dead_bullet_only(tmp_path: Path):
    (tmp_path / ".editorconfig").write_text(_EDITORCONFIG, encoding="utf-8")
    text = "# Kurallar\n\n- Use 2-space indent everywhere.\n- Run tests before pushing.\n"

    result = apply_safe_fixes(text, tmp_path)

    assert result.fixed_text == "# Kurallar\n\n- Run tests before pushing.\n"
    assert [d.line_no for d in result.removed_dead_rules] == [3]
    assert result.manual == []


def test_apply_safe_fixes_keeps_dead_rule_with_children_or_outside_bullets(tmp_path: Path):
    (tmp_path / ".editorconfig").write_text(_EDITORCONFIG, encoding="utf-8")
    text = (
        "- Use 2-space indent everywhere.\n"
        "  - but tabs in Makefiles\n"
        "\n"
        "Plain paragraph: always use 2-space indent.\n"
        "```\n- 2-space indent in a code fence\n```\n"
    )

    result = apply_safe_fixes(text, tmp_path)

    assert result.fixed_text == text
    assert not result.changed
    assert len(result.manual) == 3


def test_apply_safe_fixes_reports_unfixable_secret(tmp_path: Path):
    text = "-----BEGIN RSA PRIVATE KEY-----\nabc\n"

    result = apply_safe_fixes(text, tmp_path)

    assert not result.changed
    assert len(result.manual) == 1 and "private_key_header" in result.manual[0]


def test_fix_command_dry_run_then_apply_with_backup(tmp_path: Path):
    target = tmp_path / "CLAUDE.md"
    original = "# T\n\n- token = \"Zx9Qw8Er7Ty6Ui5\"\n"
    target.write_bytes(original.replace("\n", "\r\n").encode("utf-8"))

    dry = runner.invoke(app, ["fix", str(target), "--project-root", str(tmp_path), "--dry-run"])
    assert dry.exit_code == 0
    assert b"Zx9Qw8Er7Ty6Ui5" in target.read_bytes()

    done = runner.invoke(app, ["fix", str(target), "--project-root", str(tmp_path), "--yes"])
    assert done.exit_code == 0
    raw = target.read_bytes()
    assert b"Zx9Qw8Er7Ty6Ui5" not in raw
    assert b"<REDACTED>" in raw and b"\r\n" in raw  # CRLF korunur
    assert (tmp_path / "CLAUDE.md.bak").exists()


def test_fix_command_declined_leaves_file_untouched(tmp_path: Path):
    target = tmp_path / "CLAUDE.md"
    target.write_text('token = "Zx9Qw8Er7Ty6Ui5"\n', encoding="utf-8")

    result = runner.invoke(app, ["fix", str(target), "--project-root", str(tmp_path)], input="n\n")

    assert result.exit_code == 0
    assert "Zx9Qw8Er7Ty6Ui5" in target.read_text(encoding="utf-8")
    assert not (tmp_path / "CLAUDE.md.bak").exists()
