import re

from ado_search.grep import extract_field_text, match_field, FieldMatch, GrepResult


def test_extract_title():
    item = {"id": 1, "title": "Login fails", "description": "desc", "comments": []}
    texts = extract_field_text(item, "title")
    assert texts == [("Login fails", None, None)]


def test_extract_description():
    item = {"id": 1, "title": "T", "description": "Full description text", "comments": []}
    texts = extract_field_text(item, "description")
    assert texts == [("Full description text", None, None)]


def test_extract_comments():
    item = {
        "id": 1, "title": "T", "description": "",
        "comments": [
            {"author": "Alice", "date": "2026-03-20", "text": "First comment"},
            {"author": "Bob", "date": "2026-03-21", "text": "Second comment"},
        ],
    }
    texts = extract_field_text(item, "comments")
    assert len(texts) == 2
    assert texts[0] == ("First comment", "Alice", "2026-03-20")
    assert texts[1] == ("Second comment", "Bob", "2026-03-21")


def test_extract_missing_comments():
    item = {"id": 1, "title": "T", "description": ""}
    texts = extract_field_text(item, "comments")
    assert texts == []


def test_extract_simple_fields():
    item = {
        "id": 1, "title": "T", "description": "",
        "tags": "auth,sso,p1",
        "assigned_to": "alice@co.com",
        "area": "Proj\\Auth",
        "iteration": "Sprint 1",
        "acceptance_criteria": "Must pass SSO test",
    }
    assert extract_field_text(item, "tags") == [("auth,sso,p1", None, None)]
    assert extract_field_text(item, "assigned_to") == [("alice@co.com", None, None)]
    assert extract_field_text(item, "area") == [("Proj\\Auth", None, None)]
    assert extract_field_text(item, "iteration") == [("Sprint 1", None, None)]
    assert extract_field_text(item, "acceptance_criteria") == [("Must pass SSO test", None, None)]


def test_match_field_finds_pattern():
    pattern = re.compile(r"\d+\.\d+\.\d+\.\d+")
    matches = match_field(pattern, "description", "The IP 10.0.0.1 was blocked", context_chars=15)
    assert len(matches) == 1
    assert matches[0].field == "description"
    assert matches[0].text_matched == "10.0.0.1"
    assert "10.0.0.1" in matches[0].context
    assert matches[0].offset == 7


def test_match_field_multiple_matches():
    pattern = re.compile(r"\d+\.\d+\.\d+\.\d+")
    text = "Server 10.0.0.1 and backup 10.0.0.2 both failed"
    matches = match_field(pattern, "description", text, context_chars=10)
    assert len(matches) == 2
    assert matches[0].text_matched == "10.0.0.1"
    assert matches[1].text_matched == "10.0.0.2"


def test_match_field_context_at_string_boundaries():
    pattern = re.compile(r"hello")
    matches = match_field(pattern, "title", "hello world", context_chars=60)
    assert len(matches) == 1
    assert matches[0].context == "hello world"  # no leading ...


def test_match_field_context_truncation():
    pattern = re.compile(r"middle")
    text = "a" * 100 + "middle" + "b" * 100
    matches = match_field(pattern, "description", text, context_chars=10)
    assert len(matches) == 1
    assert matches[0].context.startswith("...")
    assert matches[0].context.endswith("...")
    assert "middle" in matches[0].context


def test_match_field_no_match():
    pattern = re.compile(r"zzzzz")
    matches = match_field(pattern, "title", "hello world", context_chars=60)
    assert matches == []


def test_match_field_case_insensitive():
    pattern = re.compile(r"hello", re.IGNORECASE)
    matches = match_field(pattern, "title", "HELLO world", context_chars=60)
    assert len(matches) == 1
    assert matches[0].text_matched == "HELLO"


def test_match_field_with_comment_metadata():
    pattern = re.compile(r"blocked")
    matches = match_field(
        pattern, "comments", "IP is blocked in staging",
        context_chars=60, comment_author="jdoe", comment_date="2026-03-20",
    )
    assert len(matches) == 1
    assert matches[0].comment_author == "jdoe"
    assert matches[0].comment_date == "2026-03-20"
