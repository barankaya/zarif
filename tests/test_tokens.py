from zarif.tokens import count_tokens, token_report


def test_count_tokens_nonempty_text():
    assert count_tokens("hello world") > 0


def test_count_tokens_empty_text_is_at_least_one_or_zero():
    # cl100k_base(tiktoken) encodes "" as 0 tokens; heuristic fallback floors at 1.
    assert count_tokens("") >= 0


def test_token_report_reduction():
    before = "This is a long and repetitive sentence that says the same thing twice, twice."
    after = "This sentence says the same thing once."
    report = token_report(before, after)
    assert report["before"] > report["after"]
    assert report["reduction"] == report["before"] - report["after"]
    assert report["reduction_pct"] > 0
