from pathlib import Path

from zarif.audit import (
    find_contradictions,
    find_dead_rules,
    render_sarif,
    run_audit,
    scan_for_secrets,
)


def test_find_dead_rules_flags_indent_style_already_in_editorconfig(tmp_path: Path):
    (tmp_path / ".editorconfig").write_text(
        "root = true\n\n[*]\nindent_style = space\nindent_size = 2\n", encoding="utf-8"
    )
    text = "Always indent with spaces, 2-space indent everywhere.\nSome unrelated rule."

    findings = find_dead_rules(text, tmp_path)

    assert len(findings) == 1
    assert findings[0].pattern_name == "indent-style"
    assert findings[0].line_no == 1


def test_find_dead_rules_flags_black_line_length(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[tool.black]\nline-length = 88\n", encoding="utf-8")
    text = "Keep max line length under 88 characters."

    findings = find_dead_rules(text, tmp_path)

    assert any(f.pattern_name == "line-length" for f in findings)


def test_find_dead_rules_empty_without_matching_config(tmp_path: Path):
    text = "Always indent with spaces, 2-space indent everywhere."

    findings = find_dead_rules(text, tmp_path)

    assert findings == []


def test_scan_for_secrets_detects_aws_key_and_generic_assignment():
    text = (
        'AWS_KEY="AKIAABCDEFGHIJKLMNOP"\n'
        'api_key: "sk-realvalue1234567890abcd"\n'
    )

    findings = scan_for_secrets(text)

    kinds = {f.kind for f in findings}
    assert "aws_access_key" in kinds
    assert "generic_api_key" in kinds


def test_scan_for_secrets_ignores_placeholders():
    text = 'password = "your-password-here"\napi_key = "changeme-example"\n'

    findings = scan_for_secrets(text)

    assert findings == []


def test_scan_for_secrets_redacts_output():
    text = 'AWS_KEY="AKIAABCDEFGHIJKLMNOP"\n'

    findings = scan_for_secrets(text)

    assert "AKIAABCDEFGHIJKLMNOP" not in findings[0].redacted_snippet


def test_find_contradictions_flags_opposite_polarity_shared_vocabulary():
    text = "Always use camelCase for variable names.\nNever use camelCase for variable names.\n"

    findings = find_contradictions(text)

    assert len(findings) == 1
    assert findings[0].line_a == 1
    assert findings[0].line_b == 2


def test_find_contradictions_empty_for_unrelated_rules():
    text = "Always write tests.\nNever commit secrets to version control.\n"

    findings = find_contradictions(text)

    assert findings == []


def test_run_audit_and_render_sarif_roundtrip(tmp_path: Path):
    (tmp_path / ".editorconfig").write_text("[*]\nindent_style = space\n", encoding="utf-8")
    text = "Always indent with spaces, 2-space indent everywhere.\nAWS_KEY=\"AKIAABCDEFGHIJKLMNOP\"\n"

    result = run_audit(text, tmp_path)
    sarif_doc = render_sarif(result, "CLAUDE.md")

    assert sarif_doc["version"] == "2.1.0"
    rule_ids = {rule["id"] for rule in sarif_doc["runs"][0]["tool"]["driver"]["rules"]}
    assert "zarif/dead-rule" in rule_ids
    assert "zarif/secret" in rule_ids
    assert len(sarif_doc["runs"][0]["results"]) == len(result.dead_rules) + len(result.secrets)
