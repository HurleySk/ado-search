from __future__ import annotations

import sqlite3
from pathlib import Path


def _sanitize_fts_query(query: str) -> str:
    """Quote each token to prevent FTS5 syntax injection."""
    tokens = query.split()
    # Escape any double-quotes inside the token by doubling them, then wrap in quotes
    return " ".join(f'"{t.replace(chr(34), chr(34) + chr(34))}"' for t in tokens if t)


class Database:
    def __init__(self, path: Path):
        self._path = path
        self._conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
        return self._conn

    def initialize(self) -> None:
        conn = self._connect()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS work_items (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                type TEXT NOT NULL,
                state TEXT NOT NULL,
                area TEXT,
                iteration TEXT,
                assigned_to TEXT,
                tags TEXT,
                priority INTEGER,
                parent_id INTEGER,
                created TEXT,
                updated TEXT
            );

            CREATE TABLE IF NOT EXISTS wiki_pages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                updated TEXT
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
                item_type,
                item_id UNINDEXED,
                title,
                description_snippet,
                tags
            );
        """)
        conn.commit()

    def upsert_work_item(self, item: dict) -> None:
        conn = self._connect()
        conn.execute(
            """INSERT INTO work_items
               (id, title, type, state, area, iteration, assigned_to, tags,
                priority, parent_id, created, updated)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                title=excluded.title, type=excluded.type, state=excluded.state,
                area=excluded.area, iteration=excluded.iteration,
                assigned_to=excluded.assigned_to, tags=excluded.tags,
                priority=excluded.priority, parent_id=excluded.parent_id,
                created=excluded.created, updated=excluded.updated
            """,
            (
                item["id"], item["title"], item["type"], item["state"],
                item["area"], item["iteration"], item["assigned_to"],
                item["tags"], item["priority"], item["parent_id"],
                item["created"], item["updated"],
            ),
        )
        conn.execute(
            "DELETE FROM search_index WHERE item_type = 'work_item' AND item_id = ?",
            (str(item["id"]),),
        )
        conn.execute(
            "INSERT INTO search_index (item_type, item_id, title, description_snippet, tags) VALUES (?, ?, ?, ?, ?)",
            ("work_item", str(item["id"]), item["title"], item.get("description_snippet", ""), item["tags"]),
        )
        conn.commit()

    def upsert_wiki_page(self, page: dict) -> None:
        conn = self._connect()
        conn.execute(
            """INSERT INTO wiki_pages (path, title, updated)
               VALUES (?, ?, ?)
               ON CONFLICT(path) DO UPDATE SET
                title=excluded.title, updated=excluded.updated
            """,
            (page["path"], page["title"], page["updated"]),
        )
        conn.execute(
            "DELETE FROM search_index WHERE item_type = 'wiki' AND item_id = ?",
            (page["path"],),
        )
        conn.execute(
            "INSERT INTO search_index (item_type, item_id, title, description_snippet, tags) VALUES (?, ?, ?, ?, ?)",
            ("wiki", page["path"], page["title"], page.get("description_snippet", ""), ""),
        )
        conn.commit()

    def delete_work_item(self, item_id: int) -> None:
        conn = self._connect()
        conn.execute("DELETE FROM work_items WHERE id = ?", (item_id,))
        conn.execute(
            "DELETE FROM search_index WHERE item_type = 'work_item' AND item_id = ?",
            (str(item_id),),
        )
        conn.commit()

    def delete_wiki_page(self, path: str) -> None:
        conn = self._connect()
        conn.execute("DELETE FROM wiki_pages WHERE path = ?", (path,))
        conn.execute(
            "DELETE FROM search_index WHERE item_type = 'wiki' AND item_id = ?",
            (path,),
        )
        conn.commit()

    def search_work_items(
        self,
        query: str,
        *,
        type_filter: str | None = None,
        state_filter: str | None = None,
        area_filter: str | None = None,
        assigned_to_filter: str | None = None,
        tag_filter: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        conn = self._connect()
        sql = """
            SELECT w.id, w.title, w.type, w.state, w.area, w.iteration,
                   w.assigned_to, w.tags, w.priority, w.parent_id,
                   w.created, w.updated, s.description_snippet
            FROM search_index s
            JOIN work_items w ON CAST(s.item_id AS INTEGER) = w.id
            WHERE s.item_type = 'work_item'
              AND search_index MATCH ?
        """
        params: list = [_sanitize_fts_query(query)]

        if type_filter:
            sql += " AND w.type = ?"
            params.append(type_filter)
        if state_filter:
            sql += " AND w.state = ?"
            params.append(state_filter)
        if area_filter:
            sql += " AND w.area LIKE ?"
            params.append(f"{area_filter}%")
        if assigned_to_filter:
            sql += " AND w.assigned_to = ?"
            params.append(assigned_to_filter)
        if tag_filter:
            sql += " AND (',' || w.tags || ',') LIKE ?"
            params.append(f"%,{tag_filter},%")

        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def search_wiki(self, query: str, *, limit: int = 20) -> list[dict]:
        conn = self._connect()
        rows = conn.execute(
            """
            SELECT w.path, w.title, w.updated, s.description_snippet
            FROM search_index s
            JOIN wiki_pages w ON s.item_id = w.path
            WHERE s.item_type = 'wiki'
              AND search_index MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (_sanitize_fts_query(query), limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_all_work_item_ids(self) -> list[int]:
        conn = self._connect()
        rows = conn.execute("SELECT id FROM work_items").fetchall()
        return [row["id"] for row in rows]

    def get_all_wiki_paths(self) -> list[str]:
        conn = self._connect()
        rows = conn.execute("SELECT path FROM wiki_pages").fetchall()
        return [row["path"] for row in rows]

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
