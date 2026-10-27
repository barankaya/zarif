from pathlib import Path

from zarif.audit import (
    find_contradictions,
    find_dead_rules,
    find_stale_references,
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


def test_find_stale_references_flags_missing_path_and_keeps_existing(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("", encoding="utf-8")
    text = "Entry point: `src/app.py`.\nOld helper lives in `src/legacy/helper.py`.\n"

    findings = find_stale_references(text, tmp_path)

    assert [(f.kind, f.reference, f.line_no) for f in findings] == [("path", "src/legacy/helper.py", 2)]


def test_find_stale_references_ignores_globs_urls_generated_dirs_and_fences(tmp_path: Path):
    text = (
        "See `src/**/*.py`, `https://example.com/a/b.html`, `dist/bundle.js`, `<dir>/x.py`.\n"
        "```\ncd missing/dir/file.txt\n```\n"
        "Plain words and/or `no_slash.py` are skipped.\n"
    )

    assert find_stale_references(text, tmp_path) == []


def test_find_stale_references_resolves_relative_to_file_dir(tmp_path: Path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "local.py").write_text("", encoding="utf-8")
    (tmp_path / "pkg" / "sub").mkdir()
    text = "Use `sub/` and `sub/missing.py`.\n"

    findings = find_stale_references(text, tmp_path, file_dir=tmp_path / "pkg")

    assert [f.reference for f in findings] == ["sub/missing.py"]


def test_find_stale_references_checks_npm_scripts(tmp_path: Path):
    (tmp_path / "package.json").write_text('{"scripts": {"build": "tsc"}}', encoding="utf-8")
    text = "Run `npm run build` then:\n```bash\npnpm run lint\n```\nAlso `npm run build -- --watch`.\n"

    findings = find_stale_references(text, tmp_path)

    assert [(f.kind, f.line_no) for f in findings] == [("npm-script", 3)]
    assert "lint" in findings[0].reason


def test_find_stale_references_skips_script_check_without_package_json(tmp_path: Path):
    assert find_stale_references("Run `npm run build`.", tmp_path) == []


def test_find_stale_references_checks_make_targets(tmp_path: Path):
    (tmp_path / "Makefile").write_text(".PHONY: test lint\ntest:\n\tpytest\nlint:\n\truff .\n", encoding="utf-8")
    text = "Run `make test`, `make deploy`, and make sure `make VAR=1` is ignored.\n"

    findings = find_stale_references(text, tmp_path)

    assert [f.reference for f in findings] == ["make deploy"]


def test_find_stale_references_skips_make_when_makefile_includes_others(tmp_path: Path):
    (tmp_path / "Makefile").write_text("include other.mk\n", encoding="utf-8")

    assert find_stale_references("Run `make anything`.", tmp_path) == []


def test_run_audit_includes_stale_references_in_sarif(tmp_path: Path):
    result = run_audit("See `docs/gone.md`.\n", tmp_path)
    sarif_doc = render_sarif(result, "CLAUDE.md")

    assert len(result.stale_references) == 1
    rule_ids = {r["id"] for r in sarif_doc["runs"][0]["tool"]["driver"]["rules"]}
    assert "zarif/stale-reference" in rule_ids
