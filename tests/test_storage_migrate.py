# -*- coding: utf-8 -*-
"""storage 迁移单测：旧库（due_date NOT NULL、无 task_type）→ 新结构。"""
import sqlite3
import unittest

from storage import migrate


OLD_SCHEMA = """
CREATE TABLE tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL,
    detail      TEXT    DEFAULT '',
    due_date    TEXT    NOT NULL,
    due_time    TEXT,
    recur       TEXT    DEFAULT 'none',
    lead_days   TEXT    DEFAULT '1,3',
    status      TEXT    DEFAULT 'pending',
    created_at  TEXT    NOT NULL,
    completed_at TEXT
);
"""

# v1.0 现役库结构（已有 task_type/last_notified_at，无 display_kind）
CURRENT_V1_SCHEMA = """
CREATE TABLE tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL,
    detail      TEXT    DEFAULT '',
    due_date    TEXT,
    due_time    TEXT,
    recur       TEXT    DEFAULT 'none',
    lead_days   TEXT    DEFAULT '1,3',
    status      TEXT    DEFAULT 'pending',
    created_at  TEXT    NOT NULL,
    completed_at TEXT,
    task_type   TEXT    DEFAULT 'short',
    last_notified_at DATE
);
"""


class TestMigrate(unittest.TestCase):
    def _old_db(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(OLD_SCHEMA)
        conn.execute(
            "INSERT INTO tasks(title,due_date,created_at) "
            "VALUES('SAIS报名','2027-03-01','2026-01-01 00:00:00')")
        conn.commit()
        return conn

    def test_migrate_adds_columns(self):
        conn = self._old_db()
        migrate(conn)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
        self.assertIn("task_type", cols)
        self.assertIn("last_notified_at", cols)

    def test_migrate_allows_null_due_date(self):
        conn = self._old_db()
        migrate(conn)
        # 旧数据完好，迁移后默认短期
        row = conn.execute("SELECT * FROM tasks WHERE title='SAIS报名'").fetchone()
        self.assertEqual(row["due_date"], "2027-03-01")
        self.assertEqual(row["task_type"], "short")
        # 新数据允许 NULL 目标日
        conn.execute(
            "INSERT INTO tasks(title,due_date,task_type,created_at) "
            "VALUES('长期事',NULL,'long','2026-01-01 00:00:00')")
        conn.commit()
        n = conn.execute(
            "SELECT COUNT(*) c FROM tasks WHERE due_date IS NULL").fetchone()["c"]
        self.assertEqual(n, 1)

    def test_migrate_idempotent(self):
        conn = self._old_db()
        migrate(conn)
        migrate(conn)  # 二次执行不报错不重复
        row = conn.execute(
            "SELECT COUNT(*) c FROM tasks WHERE title='SAIS报名'").fetchone()
        self.assertEqual(row["c"], 1)

    def test_index_exists(self):
        conn = self._old_db()
        migrate(conn)
        idx = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_tasks_type_due'").fetchone()
        self.assertIsNotNone(idx)

    def test_fresh_schema_noop(self):
        # 新建库（无 tasks 表）migrate 应直接跳过
        conn = sqlite3.connect(":memory:")
        migrate(conn)  # 不抛异常
        cols = conn.execute("PRAGMA table_info(tasks)").fetchall()
        self.assertEqual(cols, [])

    # ---------------- v2.0 增量：display_kind（规格 §6.4） ----------------

    def test_display_kind_from_current_v1_db(self):
        # 最贴真的情形：v1.0 现役库（有 task_type/last_notified_at、无
        # display_kind），内含长期任务 → 迁移后 long='goal'、short=NULL
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(CURRENT_V1_SCHEMA)
        conn.execute(
            "INSERT INTO tasks(title,due_date,created_at,task_type) "
            "VALUES('SAIS','2027-03-01','2026-01-01 00:00:00','long')")
        conn.execute(
            "INSERT INTO tasks(title,due_date,created_at,task_type) "
            "VALUES('短期事','2027-01-01','2026-01-01 00:00:00','short')")
        conn.commit()
        migrate(conn)
        rows = {r["title"]: r["display_kind"]
                for r in conn.execute("SELECT title, display_kind FROM tasks")}
        self.assertEqual(rows["SAIS"], "goal")
        self.assertIsNone(rows["短期事"])

    def test_display_kind_idempotent(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(CURRENT_V1_SCHEMA)
        conn.execute(
            "INSERT INTO tasks(title,due_date,created_at,task_type) "
            "VALUES('老长期','2027-06-01','2026-01-01 00:00:00','long')")
        conn.commit()
        migrate(conn)
        conn.execute("UPDATE tasks SET display_kind='habit'")
        conn.commit()
        migrate(conn)  # 已有列 → 不再 ALTER，也不覆盖 habit
        row = conn.execute(
            "SELECT display_kind FROM tasks WHERE title='老长期'").fetchone()
        self.assertEqual(row["display_kind"], "habit")

    def test_display_kind_survives_old_rebuild_path(self):
        # 更老的库（due_date NOT NULL）走"建新表搬数据"路径也不丢新列
        conn = self._old_db()
        migrate(conn)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
        self.assertIn("display_kind", cols)


def test_update_task_display_kind_whitelist(tmp_path, monkeypatch):
    """update_task 白名单含 display_kind（规格 §6.4 同步点）。"""
    import storage
    monkeypatch.setattr(storage, "DB_PATH", str(tmp_path / "u.db"))
    monkeypatch.setattr(storage, "_conn", None)
    tid = storage.add_task("x", None, task_type="long")
    storage.update_task(tid, display_kind="habit")
    assert storage.get_task(tid)["display_kind"] == "habit"
    storage.update_task(tid, display_kind=None)  # 可写回 NULL
    assert storage.get_task(tid)["display_kind"] is None
    storage.connect().close()
    monkeypatch.setattr(storage, "_conn", None)


if __name__ == "__main__":
    unittest.main()
