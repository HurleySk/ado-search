import json
from pathlib import Path

from ado_search.markdown import work_item_to_markdown, extract_work_item_metadata, strip_html


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def test_strip_html():
    assert strip_html("<p>Hello <b>world</b></p>") == "Hello world"
    assert strip_html("plain text") == "plain text"
    assert strip_html("<div><p>Line 1</p><p>Line 2</p></div>") == "Line 1\nLine 2"


def test_extract_work_item_metadata():
    with open(FIXTURE_DIR / "work_item_12345.json") as f:
        raw = json.load(f)
    meta = extract_work_item_metadata(raw)
    assert meta["id"] == 12345
    assert meta["title"] == "Login fails with SSO redirect"
    assert meta["type"] == "Bug"
    assert meta["state"] == "Active"
    assert meta["area"] == "MyProject\\Auth"
    assert meta["iteration"] == "MyProject\\Sprint 42"
    assert meta["assigned_to"] == "jdoe@contoso.com"
    assert meta["tags"] == "sso,authentication,p1"
    assert meta["priority"] == 1
    assert meta["parent_id"] == 12300
    assert "2026-03-15" in meta["created"]
    assert "2026-04-01" in meta["updated"]
    assert meta["description_snippet"].startswith("When a user attempts")
    assert meta["description_full"].startswith("When a user attempts")
    assert "IdP configurations" in meta["acceptance_criteria"]


def test_work_item_to_markdown():
    with open(FIXTURE_DIR / "work_item_12345.json") as f:
        raw = json.load(f)
    md = work_item_to_markdown(raw, comments=[])
    assert "---" in md
    assert "id: 12345" in md
    assert "title: Login fails with SSO redirect" in md
    assert "## Description" in md
    assert "SSO redirect fails with a 403 error" in md or "403 error" in md
    assert "## Acceptance Criteria" in md
    assert "type: Bug" in md
    assert "state: Active" in md


def test_work_item_to_markdown_with_comments():
    with open(FIXTURE_DIR / "work_item_12345.json") as f:
        raw = json.load(f)
    comments = [
        {"text": "<p>Reproduced on staging</p>", "createdBy": {"displayName": "Jane Doe"}, "createdDate": "2026-03-20T10:00:00Z"},
        {"text": "<p>Root cause found</p>", "createdBy": {"displayName": "Bob Smith"}, "createdDate": "2026-03-22T14:00:00Z"},
    ]
    md = work_item_to_markdown(raw, comments=comments)
    assert "## Comments" in md
    assert "Jane Doe" in md
    assert "Reproduced on staging" in md
    assert "Bob Smith" in md


def test_work_item_to_markdown_with_attachments():
    with open(FIXTURE_DIR / "work_item_12345.json") as f:
        raw = json.load(f)
    attachments = [
        {"name": "screenshot.png", "size": 45321, "guid": "abc", "local_path": "attachments/12345/screenshot.png"},
        {"name": "doc.pdf", "size": 1048576, "guid": "def", "local_path": "attachments/12345/doc.pdf"},
    ]
    inline_images = [
        {"guid": "ghi", "local_path": "attachments/12345/inline/ghi.png", "source_field": "description"},
    ]
    md = work_item_to_markdown(raw, attachments=attachments, inline_images=inline_images)
    assert "## Attachments" in md
    assert "screenshot.png" in md
    assert "44.3 KB" in md
    assert "doc.pdf" in md
    assert "1.0 MB" in md
    assert "## Inline Images" in md
    assert "description image" in md
    assert "attachments/12345/inline/ghi.png" in md


def test_strip_html_preserves_rewritten_img():
    html = '<p>See image:</p><img src="attachments/1/inline/abc.png" /><p>End</p>'
    text = strip_html(html)
    assert "[image: attachments/1/inline/abc.png]" in text
