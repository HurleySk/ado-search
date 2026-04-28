"""Tests for the children command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ado_search.db import Database
from ado_search.children import ChildItem, query_children, format_children, _build_tree_lines


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "index.db")
    d.initialize()
    yield d
    d.close()


def _make_item(id, type="User Story", state="Active", title="", parent_id=None, **kw):
    return {
        "id": id, "title": title or f"Item {id}", "type": type, "state": state,
        "area": "Wave 3", "iteration": "Sprint 1", "assigned_to": "user@test.com",
        "tags": "tag1", "priority": 2, "parent_id": parent_id,
        "created": "2026-01-01", "updated": "2026-04-01",
        "description": "", "acceptance_criteria": "", "story_points": None,
    }


class TestGetChildren:
    def test_direct_children(self, db):
        with db.batch():
            db.upsert_work_item(_make_item(100, "Epic", parent_id=None))
            db.upsert_work_item(_make_item(101, "Feature", parent_id=100))
            db.upsert_work_item(_make_item(102, "Feature", parent_id=100))
            db.upsert_work_item(_make_item(103, "User Story", parent_id=101))

        children = db.get_children(100)
        assert len(children) == 2
        assert {c["id"] for c in children} == {101, 102}

    def test_recursive(self, db):
        with db.batch():
            db.upsert_work_item(_make_item(100, "Epic", parent_id=None))
            db.upsert_work_item(_make_item(101, "Feature", parent_id=100))
            db.upsert_work_item(_make_item(102, "User Story", parent_id=101))
            db.upsert_work_item(_make_item(103, "Task", parent_id=102))

        desc = db.get_children(100, recursive=True)
        assert len(desc) == 3
        assert {d["id"] for d in desc} == {101, 102, 103}

    def test_type_filter(self, db):
        with db.batch():
            db.upsert_work_item(_make_item(100, "Epic", parent_id=None))
            db.upsert_work_item(_make_item(101, "Feature", parent_id=100))
            db.upsert_work_item(_make_item(102, "Bug", parent_id=100))

        children = db.get_children(100, type_filter="Feature")
        assert len(children) == 1
        assert children[0]["id"] == 101

    def test_state_filter(self, db):
        with db.batch():
            db.upsert_work_item(_make_item(100, "Epic", parent_id=None))
            db.upsert_work_item(_make_item(101, "Feature", state="Active", parent_id=100))
            db.upsert_work_item(_make_item(102, "Feature", state="Closed", parent_id=100))

        children = db.get_children(100, state_filter="Active")
        assert len(children) == 1
        assert children[0]["id"] == 101

    def test_no_children(self, db):
        db.upsert_work_item(_make_item(100, "Epic", parent_id=None))
        assert db.get_children(100) == []

    def test_depth_tracking(self, db):
        with db.batch():
            db.upsert_work_item(_make_item(100, "Epic", parent_id=None))
            db.upsert_work_item(_make_item(101, "Feature", parent_id=100))
            db.upsert_work_item(_make_item(102, "User Story", parent_id=101))

        desc = db.get_children(100, recursive=True)
        depths = {d["id"]: d["depth"] for d in desc}
        assert depths[101] == 1
        assert depths[102] == 2


class TestGetClosedDates:
    def test_closed_dates(self, db):
        with db.batch():
            db.upsert_work_item(_make_item(101, state="Closed", parent_id=100))
            db.upsert_state_changes(101, [
                {"from": "Active", "to": "Closed", "date": "2026-03-15", "by": "user"},
            ])

        closed = db.get_closed_dates([101, 999])
        assert closed == {101: "2026-03-15"}

    def test_no_closed(self, db):
        with db.batch():
            db.upsert_work_item(_make_item(101, state="Active", parent_id=100))

        assert db.get_closed_dates([101]) == {}

    def test_empty_ids(self, db):
        assert db.get_closed_dates([]) == {}


class TestQueryChildren:
    def test_basic(self, db):
        with db.batch():
            db.upsert_work_item(_make_item(100, "Epic", parent_id=None))
            db.upsert_work_item(_make_item(101, "Feature", parent_id=100))

        items = query_children(db, 100)
        assert len(items) == 1
        assert isinstance(items[0], ChildItem)
        assert items[0].id == 101

    def test_with_closed_date(self, db):
        with db.batch():
            db.upsert_work_item(_make_item(100, "Epic", parent_id=None))
            db.upsert_work_item(_make_item(101, "Feature", state="Closed", parent_id=100))
            db.upsert_state_changes(101, [
                {"from": "Active", "to": "Closed", "date": "2026-03-10", "by": "user"},
            ])

        items = query_children(db, 100, include_closed_date=True)
        assert items[0].closed_date == "2026-03-10"


class TestFormatChildren:
    def _items(self):
        return [
            ChildItem(101, "Feature", "Active", "Build API", "alice@test.com",
                      "Wave 3", "Sprint 1", "api", 100, 1),
            ChildItem(102, "Feature", "Closed", "Build UI", "bob@test.com",
                      "Wave 3", "Sprint 2", "ui", 100, 1, "2026-03-15"),
        ]

    def test_compact(self):
        out = format_children(self._items(), fmt="compact", parent_id=100)
        assert "#101" in out
        assert "#102" in out
        assert "2 items under #100" in out

    def test_tree(self):
        items = [
            ChildItem(101, "Feature", "Active", "Parent", "", "", "", "", 100, 1),
            ChildItem(102, "User Story", "Active", "Child", "", "", "", "", 101, 2),
        ]
        out = format_children(items, fmt="tree", parent_id=100)
        assert "#101 Feature [Active]" in out
        assert "  #102 User Story [Active]" in out

    def test_json(self):
        items = self._items()
        out = format_children(items, fmt="json", parent_id=100)
        parsed = json.loads(out)
        assert len(parsed) == 2
        assert parsed[0]["id"] == 101

    def test_summary(self):
        out = format_children(self._items(), fmt="compact", parent_id=100)
        assert "2 Features" in out
