import json
from pathlib import Path

from ado_search.jsonl import read_jsonl, write_jsonl, merge_jsonl


def test_write_jsonl_sorts_by_key(tmp_path):
    path = tmp_path / "items.jsonl"
    items = {3: {"id": 3, "title": "C"}, 1: {"id": 1, "title": "A"}, 2: {"id": 2, "title": "B"}}
    write_jsonl(path, items, sort_key="id")
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 3
    assert json.loads(lines[0])["id"] == 1
    assert json.loads(lines[1])["id"] == 2
    assert json.loads(lines[2])["id"] == 3


def test_read_jsonl_returns_dict(tmp_path):
    path = tmp_path / "items.jsonl"
    path.write_text(
        '{"id": 1, "title": "A"}\n{"id": 2, "title": "B"}\n',
        encoding="utf-8",
    )
    result = read_jsonl(path, key="id")
    assert result == {1: {"id": 1, "title": "A"}, 2: {"id": 2, "title": "B"}}


def test_read_jsonl_missing_file(tmp_path):
    path = tmp_path / "missing.jsonl"
    result = read_jsonl(path, key="id")
    assert result == {}


def test_merge_jsonl_adds_and_updates(tmp_path):
    path = tmp_path / "items.jsonl"
    path.write_text('{"id": 1, "title": "Old"}\n{"id": 2, "title": "Keep"}\n', encoding="utf-8")
    new_items = {1: {"id": 1, "title": "Updated"}, 3: {"id": 3, "title": "New"}}
    merged = merge_jsonl(path, new_items, key="id")
    assert merged[1]["title"] == "Updated"
    assert merged[2]["title"] == "Keep"
    assert merged[3]["title"] == "New"


def test_merge_jsonl_with_removals(tmp_path):
    path = tmp_path / "items.jsonl"
    path.write_text('{"id": 1, "title": "A"}\n{"id": 2, "title": "B"}\n', encoding="utf-8")
    new_items = {1: {"id": 1, "title": "A"}}
    merged = merge_jsonl(path, new_items, key="id", remove_keys={2})
    assert 2 not in merged
    assert 1 in merged


def test_write_jsonl_atomic(tmp_path):
    path = tmp_path / "items.jsonl"
    path.write_text('{"id": 1, "title": "Original"}\n', encoding="utf-8")
    write_jsonl(path, {1: {"id": 1, "title": "New"}}, sort_key="id")
    assert json.loads(path.read_text(encoding="utf-8").strip())["title"] == "New"


def test_write_jsonl_string_sort_key(tmp_path):
    path = tmp_path / "pages.jsonl"
    items = {
        "/Z/Page": {"path": "/Z/Page", "title": "Z"},
        "/A/Page": {"path": "/A/Page", "title": "A"},
    }
    write_jsonl(path, items, sort_key="path")
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    assert json.loads(lines[0])["path"] == "/A/Page"
    assert json.loads(lines[1])["path"] == "/Z/Page"
