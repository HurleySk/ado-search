import re

from ado_search.grep import extract_field_text, FieldMatch, GrepResult


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
