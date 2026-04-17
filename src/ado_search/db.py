from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path


def _sanitize_fts_query(query: str) -> str:
    """Quote each token to prevent FTS5 syntax injection."""
    tokens = query.split()
    # Escape any double-quotes inside the token by doubling them, then wrap in quotes
    return " ".join(f'"{t.replace(chr(34), chr(34) + chr(34))}"' for t in tokens if t)


_BATCH_CHUNK = 500  # stay under SQLite's SQLITE_MAX_VARIABLE_NUMBER limit


class Database:
    def __init__(self, path: Path):
        self._path = path
        self._conn: sqlite3.Connection | None = None
        self._in_batch: bool = False
        self._skip_fts_delete: bool = False

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
        return self._conn

    @contextmanager
    def batch(self):
        """Batch operations into a single transaction. Commits once at the end."""
        conn = self._connect()
        self._in_batch = True
        try:
            yield
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._in_batch = False

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
                updated TEXT,
                description TEXT DEFAULT '',
                acceptance_criteria TEXT DEFAULT '',
                story_points REAL DEFAULT NULL
            );

            CREATE TABLE IF NOT EXISTS wiki_pages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                updated TEXT,
                content TEXT DEFAULT ''
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
                item_type,
                item_id UNINDEXED,
                title,
                description_snippet,
                tags
            );

            CREATE TABLE IF NOT EXISTS work_item_state_changes (
                item_id INTEGER NOT NULL,
                from_state TEXT NOT NULL,
                to_state TEXT NOT NULL,
                changed_date TEXT NOT NULL,
                changed_by TEXT,
                PRIMARY KEY (item_id, changed_date, to_state)
            );
        """)
        for col, col_type, default in [
            ("description", "TEXT", "''"),
            ("acceptance_criteria", "TEXT", "''"),
            ("story_points", "REAL", "NULL"),
        ]:
            try:
                conn.execute(f"ALTER TABLE work_items ADD COLUMN {col} {col_type} DEFAULT {default}")
            except Exception:
                pass  # column already exists
        try:
            conn.execute("ALTER TABLE wiki_pages ADD COLUMN content TEXT DEFAULT ''")
        except Exception:
            pass
        conn.commit()

    def upsert_work_item(self, item: dict) -> None:
        conn = self._connect()
        conn.execute(
            """INSERT INTO work_items
               (id, title, type, state, area, iteration, assigned_to, tags,
                priority, parent_id, created, updated, description, acceptance_criteria,
                story_points)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                title=excluded.title, type=excluded.type, state=excluded.state,
                area=excluded.area, iteration=excluded.iteration,
                assigned_to=excluded.assigned_to, tags=excluded.tags,
                priority=excluded.priority, parent_id=excluded.parent_id,
                created=excluded.created, updated=excluded.updated,
                description=excluded.description,
                acceptance_criteria=excluded.acceptance_criteria,
                story_points=excluded.story_points
            """,
            (
                item["id"], item["title"], item["type"], item["state"],
                item["area"], item["iteration"], item["assigned_to"],
                item["tags"], item["priority"], item["parent_id"],
                item["created"], item["updated"],
                item.get("description", ""), item.get("acceptance_criteria", ""),
                item.get("story_points"),
            ),
        )
        if not self._skip_fts_delete:
            conn.execute(
                "DELETE FROM search_index WHERE item_type = 'work_item' AND item_id = ?",
                (str(item["id"]),),
            )
        conn.execute(
            "INSERT INTO search_index (item_type, item_id, title, description_snippet, tags) VALUES (?, ?, ?, ?, ?)",
            ("work_item", str(item["id"]), item["title"], item.get("description_snippet", ""), item["tags"]),
        )
        if not self._in_batch:
            conn.commit()

    def upsert_wiki_page(self, page: dict) -> None:
        conn = self._connect()
        conn.execute(
            """INSERT INTO wiki_pages (path, title, updated, content)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(path) DO UPDATE SET
                title=excluded.title, updated=excluded.updated,
                content=excluded.content
            """,
            (page["path"], page["title"], page["updated"], page.get("content", "")),
        )
        if not self._skip_fts_delete:
            conn.execute(
                "DELETE FROM search_index WHERE item_type = 'wiki' AND item_id = ?",
                (page["path"],),
            )
        conn.execute(
            "INSERT INTO search_index (item_type, item_id, title, description_snippet, tags) VALUES (?, ?, ?, ?, ?)",
            ("wiki", page["path"], page["title"], page.get("description_snippet", ""), ""),
        )
        if not self._in_batch:
            conn.commit()

    def delete_work_item(self, item_id: int) -> None:
        conn = self._connect()
        conn.execute("DELETE FROM work_items WHERE id = ?", (item_id,))
        conn.execute(
            "DELETE FROM search_index WHERE item_type = 'work_item' AND item_id = ?",
            (str(item_id),),
        )
        conn.execute("DELETE FROM work_item_state_changes WHERE item_id = ?", (item_id,))
        if not self._in_batch:
            conn.commit()

    def delete_work_items_batch(self, item_ids: list[int]) -> None:
        if not item_ids:
            return
        conn = self._connect()
        for i in range(0, len(item_ids), _BATCH_CHUNK):
            chunk = item_ids[i:i + _BATCH_CHUNK]
            placeholders = ",".join("?" * len(chunk))
            conn.execute(f"DELETE FROM work_items WHERE id IN ({placeholders})", chunk)
            str_chunk = [str(x) for x in chunk]
            conn.execute(
                f"DELETE FROM search_index WHERE item_type = 'work_item' AND item_id IN ({placeholders})",
                str_chunk,
            )
            conn.execute(
                f"DELETE FROM work_item_state_changes WHERE item_id IN ({placeholders})",
                chunk,
            )
        if not self._in_batch:
            conn.commit()

    def delete_wiki_page(self, path: str) -> None:
        conn = self._connect()
        conn.execute("DELETE FROM wiki_pages WHERE path = ?", (path,))
        conn.execute(
            "DELETE FROM search_index WHERE item_type = 'wiki' AND item_id = ?",
            (path,),
        )
        if not self._in_batch:
            conn.commit()

    def delete_wiki_pages_batch(self, paths: list[str]) -> None:
        if not paths:
            return
        conn = self._connect()
        for i in range(0, len(paths), _BATCH_CHUNK):
            chunk = paths[i:i + _BATCH_CHUNK]
            placeholders = ",".join("?" * len(chunk))
            conn.execute(f"DELETE FROM wiki_pages WHERE path IN ({placeholders})", chunk)
            conn.execute(
                f"DELETE FROM search_index WHERE item_type = 'wiki' AND item_id IN ({placeholders})",
                chunk,
            )
        if not self._in_batch:
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

    def get_work_item(self, item_id: int) -> dict | None:
        conn = self._connect()
        row = conn.execute("SELECT * FROM work_items WHERE id = ?", (item_id,)).fetchone()
        return dict(row) if row else None

    def get_wiki_page(self, path: str) -> dict | None:
        conn = self._connect()
        row = conn.execute("SELECT * FROM wiki_pages WHERE path = ?", (path,)).fetchone()
        return dict(row) if row else None

    def reindex_from_jsonl(self, work_items_path: Path, wiki_pages_path: Path) -> None:
        """Rebuild the entire DB index from JSONL files."""
        from ado_search.jsonl import iter_jsonl
        from ado_search.markdown import make_snippet

        conn = self._connect()
        conn.execute("DELETE FROM work_items")
        conn.execute("DELETE FROM wiki_pages")
        conn.execute("DELETE FROM search_index")
        conn.execute("DELETE FROM work_item_state_changes")
        conn.commit()

        self._skip_fts_delete = True
        try:
            with self.batch():
                for item in iter_jsonl(work_items_path):
                    snippet = make_snippet(item.get("description", ""))
                    att_names = " ".join(
                        a["name"] for a in item.get("attachments", []) if a.get("name")
                    )
                    if att_names:
                        snippet = f"{snippet} [attachments: {att_names}]"
                    item.setdefault("description_snippet", snippet)
                    self.upsert_work_item(item)
                    if item.get("state_history"):
                        self.upsert_state_changes(item["id"], item["state_history"])
                for page in iter_jsonl(wiki_pages_path):
                    page.setdefault("description_snippet", make_snippet(page.get("content", "")))
                    self.upsert_wiki_page(page)
        finally:
            self._skip_fts_delete = False

    def upsert_state_changes(self, item_id: int, changes: list[dict]) -> None:
        conn = self._connect()
        conn.execute("DELETE FROM work_item_state_changes WHERE item_id = ?", (item_id,))
        for c in changes:
            conn.execute(
                """INSERT OR REPLACE INTO work_item_state_changes
                   (item_id, from_state, to_state, changed_date, changed_by)
                   VALUES (?, ?, ?, ?, ?)""",
                (item_id, c["from"], c["to"], c["date"], c.get("by", "")),
            )
        if not self._in_batch:
            conn.commit()

    def get_state_changes(self, item_id: int) -> list[dict]:
        conn = self._connect()
        rows = conn.execute(
            """SELECT item_id, from_state, to_state, changed_date, changed_by
               FROM work_item_state_changes
               WHERE item_id = ?
               ORDER BY changed_date""",
            (item_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_all_state_changes(self) -> list[dict]:
        conn = self._connect()
        rows = conn.execute(
            """SELECT item_id, from_state, to_state, changed_date, changed_by
               FROM work_item_state_changes
               ORDER BY item_id, changed_date"""
        ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
