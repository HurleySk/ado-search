import json
import re
from pathlib import Path

from ado_search.grep import extract_field_text, match_field, FieldMatch, GrepResult, grep_work_items, format_grep_results


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


# ---------------------------------------------------------------------------
# grep_work_items tests
# ---------------------------------------------------------------------------

def _write_test_jsonl(path: Path) -> None:
    items = [
        {
            "id": 100, "title": "Login fails with IP 10.0.0.1",
            "type": "Bug", "state": "Active",
            "area": "Proj\\Auth", "iteration": "Sprint 1",
            "assigned_to": "alice@co.com", "tags": "auth,sso",
            "priority": 1, "parent_id": None,
            "created": "2026-01-01", "updated": "2026-01-15",
            "description": "The server at 10.0.0.1 rejects SSO requests. Fallback to 10.0.0.2 also fails.",
            "acceptance_criteria": "SSO must work",
            "comments": [
                {"author": "Bob", "date": "2026-01-16", "text": "Confirmed 10.0.0.1 is blocked."},
            ],
        },
        {
            "id": 200, "title": "Add MFA support",
            "type": "User Story", "state": "New",
            "area": "Proj\\Auth", "iteration": "Sprint 2",
            "assigned_to": "bob@co.com", "tags": "mfa",
            "priority": 2, "parent_id": None,
            "created": "2026-02-01", "updated": "2026-02-10",
            "description": "Users want multi-factor authentication.",
            "acceptance_criteria": "",
        },
    ]
    with path.open("w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item) + "\n")


def test_grep_finds_ip_pattern(tmp_path):
    jsonl_path = tmp_path / "work-items.jsonl"
    _write_test_jsonl(jsonl_path)
    results, warnings = grep_work_items(
        jsonl_path=jsonl_path,
        pattern=re.compile(r"\d+\.\d+\.\d+\.\d+"),
        fields=["title", "description", "comments"],
    )
    assert len(results) == 1
    assert results[0].item_id == 100
    # title has 1 match, description has 2, comments has 1 = 4 total
    assert len(results[0].matches) == 4
    field_names = [m.field for m in results[0].matches]
    assert "title" in field_names
    assert "description" in field_names
    assert "comments" in field_names


def test_grep_field_scoping(tmp_path):
    jsonl_path = tmp_path / "work-items.jsonl"
    _write_test_jsonl(jsonl_path)
    results, _ = grep_work_items(
        jsonl_path=jsonl_path,
        pattern=re.compile(r"\d+\.\d+\.\d+\.\d+"),
        fields=["comments"],
    )
    assert len(results) == 1
    assert all(m.field == "comments" for m in results[0].matches)


def test_grep_no_matches(tmp_path):
    jsonl_path = tmp_path / "work-items.jsonl"
    _write_test_jsonl(jsonl_path)
    results, _ = grep_work_items(
        jsonl_path=jsonl_path,
        pattern=re.compile(r"zzzznotfound"),
        fields=["title", "description", "comments"],
    )
    assert results == []


def test_grep_with_candidate_ids(tmp_path):
    jsonl_path = tmp_path / "work-items.jsonl"
    _write_test_jsonl(jsonl_path)
    results, _ = grep_work_items(
        jsonl_path=jsonl_path,
        pattern=re.compile(r".*", re.DOTALL),
        fields=["title"],
        candidate_ids={200},
    )
    assert len(results) == 1
    assert results[0].item_id == 200


def test_grep_limit(tmp_path):
    jsonl_path = tmp_path / "work-items.jsonl"
    _write_test_jsonl(jsonl_path)
    results, _ = grep_work_items(
        jsonl_path=jsonl_path,
        pattern=re.compile(r".*", re.DOTALL),
        fields=["title"],
        limit=1,
    )
    assert len(results) == 1


def test_grep_warns_missing_comments(tmp_path):
    jsonl_path = tmp_path / "work-items.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as f:
        f.write(json.dumps({
            "id": 1, "title": "No comments", "type": "Bug", "state": "Active",
            "area": "", "iteration": "", "assigned_to": "", "tags": "",
            "priority": 1, "parent_id": None, "created": "2026-01-01",
            "updated": "2026-01-01", "description": "desc",
        }) + "\n")
    results, warnings = grep_work_items(
        jsonl_path=jsonl_path,
        pattern=re.compile(r"desc"),
        fields=["title", "description", "comments"],
    )
    assert len(warnings) == 1
    assert "comments" in warnings[0].lower()


# ---------------------------------------------------------------------------
# format_grep_results tests
# ---------------------------------------------------------------------------

def _make_result():
    return GrepResult(
        item_id=12345,
        title="Login fails with SSO redirect",
        item_type="Bug",
        state="Active",
        matches=[
            FieldMatch(field="description", text_matched="10.0.0.1",
                       context="...the IP address 10.0.0.1 was rejected by the...",
                       offset=142),
            FieldMatch(field="comments", text_matched="10.0.0.1",
                       context="...confirmed 10.0.0.1 is blocked in staging...",
                       offset=31, comment_author="jdoe", comment_date="2026-03-20"),
        ],
    )


def test_format_compact():
    result = _make_result()
    output = format_grep_results([result], fmt="compact")
    assert "#12345" in output
    assert "Bug" in output
    assert "[Active]" in output
    assert "[description]" in output
    assert "[comment by jdoe, 2026-03-20]" in output
    assert "1 item matched" in output


def test_format_brief():
    result = _make_result()
    output = format_grep_results([result], fmt="brief")
    assert "#12345" in output
    assert "description" in output
    assert "comment" in output
    # Should NOT have context snippets
    assert "10.0.0.1" not in output or "rejected" not in output


def test_format_json():
    result = _make_result()
    output = format_grep_results([result], fmt="json")
    parsed = json.loads(output)
    assert len(parsed) == 1
    assert parsed[0]["id"] == 12345
    assert len(parsed[0]["matches"]) == 2
    assert parsed[0]["matches"][1]["comment_author"] == "jdoe"


def test_format_compact_summary_plural():
    r1 = GrepResult(item_id=1, title="A", item_type="Bug", state="Active",
                    matches=[FieldMatch("title", "x", "x", 0)])
    r2 = GrepResult(item_id=2, title="B", item_type="Task", state="New",
                    matches=[FieldMatch("title", "y", "y", 0),
                             FieldMatch("description", "y", "y", 5)])
    output = format_grep_results([r1, r2], fmt="compact")
    assert "2 items matched" in output
    assert "3 total matches" in output
