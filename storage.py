# -*- coding: utf-8 -*-
"""SQLite 本地存储层：任务、历程日志、提醒去重、设置项。

所有时间日期统一使用本地时间的 ISO 字符串：
- due_date:  YYYY-MM-DD
- due_time:  HH:MM 或 None
- 时间戳:    YYYY-MM-DD HH:MM:SS
"""
import os
import sqlite3
import sys
import threading
from datetime import date, datetime, timedelta

from countdown import add_months as _add_months, days_in_month as _days_in_month


def _app_base() -> str:
    """软件根目录：PyInstaller 打包后是 exe 所在目录（data/ 与 exe 同级），
    开发态是本文件所在目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


DB_DIR = os.path.join(_app_base(), "data")
DB_PATH = os.path.join(DB_DIR, "calendar.db")

# 多线程读写同一把锁（后台定时检查与 UI 都会访问）
_lock = threading.RLock()
_conn = None

RECUR_NONE = "none"
RECUR_DAILY = "daily"
RECUR_WEEKLY = "weekly"
RECUR_MONTHLY = "monthly"
RECUR_YEARLY = "yearly"

RECUR_LABEL = {
    RECUR_NONE: "不重复",
    RECUR_DAILY: "每天",
    RECUR_WEEKLY: "每周",
    RECUR_MONTHLY: "每月",
    RECUR_YEARLY: "每年",
}

STATUS_PENDING = "pending"
STATUS_DONE = "done"


def connect():
    """惰性初始化数据库连接（check_same_thread=False + 应用层锁）。"""
    global _conn
    with _lock:
        if _conn is None:
            os.makedirs(DB_DIR, exist_ok=True)
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _create_tables(_conn)
            migrate(_conn)
        return _conn


def migrate(conn):
    """增量迁移（幂等）：加 task_type/last_notified_at 列；重建表放开
    due_date NOT NULL（长期任务允许无目标日）；补右侧面板主查询索引。"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
    if not cols:
        return  # 空库（表还没建）
    if "task_type" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN task_type TEXT DEFAULT 'short'")
    if "last_notified_at" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN last_notified_at DATE")
    if "display_kind" not in cols:
        # v2.0 唯一新增列：纯展示用途（long='goal'/'habit'，short=NULL）
        conn.execute("ALTER TABLE tasks ADD COLUMN display_kind TEXT")
        conn.execute(
            "UPDATE tasks SET display_kind = 'goal' WHERE task_type = 'long'")
    ddl = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tasks'"
    ).fetchone()[0]
    if "DUE_DATE TEXT NOT NULL" in " ".join(ddl.upper().split()):
        # SQLite 不能直接改列约束，按官方流程重建表
        conn.executescript(
            """
            CREATE TABLE tasks_new (
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
                last_notified_at DATE,
                display_kind TEXT
            );
            INSERT INTO tasks_new(id,title,detail,due_date,due_time,recur,lead_days,
                                  status,created_at,completed_at,task_type,last_notified_at,display_kind)
                SELECT id,title,detail,due_date,due_time,recur,lead_days,
                       status,created_at,completed_at,task_type,last_notified_at,display_kind
                FROM tasks;
            DROP TABLE tasks;
            ALTER TABLE tasks_new RENAME TO tasks;
            """
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tasks_type_due "
        "ON tasks(status, task_type, due_date)")
    conn.commit()


def _create_tables(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS tasks (
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

        CREATE TABLE IF NOT EXISTS journals (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id   INTEGER NOT NULL,
            content   TEXT    NOT NULL,
            created_at TEXT   NOT NULL,
            FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS reminder_log (
            task_id   INTEGER NOT NULL,
            log_date  TEXT    NOT NULL,   -- 实际提醒的是哪一天的周期实例
            shown_at  TEXT    NOT NULL,
            PRIMARY KEY (task_id, log_date)
        );

        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )
    conn.commit()


# ---------------- 重复规则 ----------------

def next_occurrence(due: date, recur: str, ref: date) -> date:
    """以 due 为锚点，返回 >= ref 的最近一次发生日期；非重复直接返回 due。"""
    if recur == RECUR_NONE:
        return due
    if ref <= due:
        return due
    delta = ref - due
    if recur == RECUR_DAILY:
        return due + timedelta(days=delta.days)
    if recur == RECUR_WEEKLY:
        weeks = (delta.days + 6) // 7
        return due + timedelta(weeks=weeks)
    if recur == RECUR_MONTHLY:
        month_index = (due.year * 12 + due.month - 1)
        target_index = ref.year * 12 + ref.month - 1
        k = target_index - month_index
        d = _add_months(due, k)
        if d < ref:
            d = _add_months(due, k + 1)
        return d
    if recur == RECUR_YEARLY:
        k = ref.year - due.year
        d = _replace_year(due, due.year + k)
        if d < ref:
            d = _replace_year(due, due.year + k + 1)
        return d
    return due


def _replace_year(d: date, y: int) -> date:
    return date(y, d.month, min(d.day, _days_in_month(y, d.month)))


def occurs_on(due: date, recur: str, day: date) -> bool:
    """任务（按重复规则）是否落在某一天。"""
    if recur == RECUR_NONE:
        return due == day
    if day < due:
        return False
    if recur == RECUR_DAILY:
        return True
    if recur == RECUR_WEEKLY:
        return (day - due).days % 7 == 0
    if recur == RECUR_MONTHLY:
        return day.day == due.day or (
            day.day == _days_in_month(day.year, day.month) and due.day > day.day
        )
    if recur == RECUR_YEARLY:
        return (day.month == due.month and day.day == due.day) or (
            day.month == due.month
            and day.day == _days_in_month(day.year, day.month)
            and due.day > day.day
        )
    return False


# ---------------- 任务 CRUD ----------------

def _row_to_dict(row: sqlite3.Row):
    return dict(row)


def add_task(title, due_date, due_time=None, detail="",
             recur=RECUR_NONE, lead_days="1,3", status=STATUS_PENDING,
             task_type="short"):
    with _lock:
        conn = connect()
        cur = conn.execute(
            """INSERT INTO tasks(title, detail, due_date, due_time, recur,
                                 lead_days, status, created_at, task_type)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (title.strip(), detail, due_date, due_time, recur,
             lead_days, status, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             task_type),
        )
        conn.commit()
        return cur.lastrowid


def update_task(task_id, **fields):
    allowed = {"title", "detail", "due_date", "due_time", "recur",
               "lead_days", "status", "completed_at", "task_type",
               "display_kind"}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k} = ?")
            vals.append(v)
    if not sets:
        return
    vals.append(task_id)
    with _lock:
        conn = connect()
        conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", vals)
        conn.commit()


def delete_task(task_id):
    with _lock:
        conn = connect()
        conn.execute("DELETE FROM journals WHERE task_id = ?", (task_id,))
        conn.execute("DELETE FROM reminder_log WHERE task_id = ?", (task_id,))
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        conn.commit()


def complete_task(task_id, today=None):
    """标记完成：一次性任务置 done；重复任务把锚点推进到下一个周期。"""
    today = today or date.today()
    with _lock:
        conn = connect()
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            return
        if row["recur"] == RECUR_NONE or not row["due_date"]:
            # 重复任务没有锚点日期时兜底直接完成
            conn.execute(
                "UPDATE tasks SET status = 'done', completed_at = ? WHERE id = ?",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), task_id),
            )
        else:
            due = datetime.strptime(row["due_date"], "%Y-%m-%d").date()
            nxt = next_occurrence(due, row["recur"], today + timedelta(days=1))
            conn.execute(
                "UPDATE tasks SET due_date = ?, status = 'pending', completed_at = ? WHERE id = ?",
                (nxt.isoformat(),
                 datetime.now().strftime("%Y-%m-%d %H:%M:%S"), task_id),
            )
            # 旧周期的提醒记录作废
            conn.execute("DELETE FROM reminder_log WHERE task_id = ?", (task_id,))
        conn.commit()


def reopen_task(task_id):
    with _lock:
        conn = connect()
        conn.execute(
            "UPDATE tasks SET status = 'pending', completed_at = NULL WHERE id = ?",
            (task_id,),
        )
        conn.commit()


def get_task(task_id):
    with _lock:
        conn = connect()
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _row_to_dict(row) if row else None


def all_tasks(include_done=False):
    with _lock:
        conn = connect()
        sql = "SELECT * FROM tasks"
        if not include_done:
            sql += " WHERE status = 'pending'"
        sql += " ORDER BY COALESCE(due_date,'9999-12-31'), COALESCE(due_time,'99:99'), id"
        return [_row_to_dict(r) for r in conn.execute(sql)]


def panel_tasks(today=None):
    """右侧待办面板数据：全部长期（常驻）+ 7 天内到期的短期，不含已完成。
    有目标日按 due_date 升序，无目标日排最后。"""
    today = today or date.today()
    horizon = (today + timedelta(days=7)).isoformat()
    with _lock:
        conn = connect()
        rows = conn.execute(
            """SELECT * FROM tasks
               WHERE status = 'pending'
                 AND (task_type = 'long'
                      OR (due_date IS NOT NULL AND due_date <= ?))
               ORDER BY (due_date IS NULL) DESC, due_date ASC,
                        COALESCE(due_time,'99:99'), id""",
            (horizon,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def long_tasks_for_daily(today=None):
    """今天还没弹过每日提醒的长期任务（有目标日、未完成）。"""
    today = today or date.today()
    with _lock:
        conn = connect()
        rows = conn.execute(
            """SELECT * FROM tasks
               WHERE status = 'pending' AND task_type = 'long'
                 AND due_date IS NOT NULL
                 AND (last_notified_at IS NULL OR last_notified_at < ?)""",
            (today.isoformat(),),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def mark_daily_notified(task_ids, today=None):
    """长期任务每日提醒弹过即写回，保证同日多次启动只弹一次。"""
    today = (today or date.today()).isoformat()
    with _lock:
        conn = connect()
        conn.executemany(
            "UPDATE tasks SET last_notified_at = ? WHERE id = ?",
            [(today, tid) for tid in task_ids])
        conn.commit()


def tasks_on(day: date, include_done=True):
    """某一天的任务：一次性命中当天，或重复任务当天发生。"""
    result = []
    with _lock:
        conn = connect()
        rows = conn.execute("SELECT * FROM tasks ORDER BY id").fetchall()
    for row in rows:
        t = _row_to_dict(row)
        if not t["due_date"]:
            continue  # 长期任务无目标日：不落日历
        due = datetime.strptime(t["due_date"], "%Y-%m-%d").date()
        if occurs_on(due, t["recur"], day):
            if t["status"] == STATUS_DONE and not include_done:
                continue
            result.append(t)
    result.sort(key=lambda t: (t["due_time"] or "99:99", t["id"]))
    return result


def month_has_task(year: int, month: int) -> set:
    """返回该月有任务标记的日期集合（供日历画点）。"""
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    days = set()
    for t in all_tasks(include_done=False):
        if not t["due_date"]:
            continue  # 长期任务无目标日：日历无圆点
        due = datetime.strptime(t["due_date"], "%Y-%m-%d").date()
        if t["recur"] == RECUR_NONE:
            if start <= due <= end:
                days.add(due.day)
            continue
        d = max(due, start)
        # 步长策略，避免对每天任务逐天循环过多
        step = 1
        if t["recur"] == RECUR_YEARLY:
            step = 366
        cursor = next_occurrence(due, t["recur"], start)
        while cursor <= end:
            if start <= cursor <= end:
                days.add(cursor.day)
            if t["recur"] == RECUR_WEEKLY:
                cursor += timedelta(weeks=1)
            elif t["recur"] == RECUR_MONTHLY:
                cursor = next_occurrence(due, RECUR_MONTHLY,
                                         cursor + timedelta(days=1))
            elif t["recur"] == RECUR_YEARLY:
                cursor = next_occurrence(due, RECUR_YEARLY,
                                         cursor + timedelta(days=1))
            else:
                cursor += timedelta(days=step)
    return days


# ---------------- 历程 ----------------

def add_journal(task_id, content):
    content = content.strip()
    if not content:
        return
    with _lock:
        conn = connect()
        conn.execute(
            "INSERT INTO journals(task_id, content, created_at) VALUES(?,?,?)",
            (task_id, content,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()


def journals_of(task_id):
    with _lock:
        conn = connect()
        rows = conn.execute(
            "SELECT * FROM journals WHERE task_id = ? ORDER BY id DESC", (task_id,)
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


# ---------------- 提醒去重 ----------------

def already_reminded(task_id, log_date: str) -> bool:
    with _lock:
        conn = connect()
        row = conn.execute(
            "SELECT 1 FROM reminder_log WHERE task_id = ? AND log_date = ?",
            (task_id, log_date),
        ).fetchone()
        return row is not None


def mark_reminded(task_ids_logdates):
    """[(task_id, log_date), ...] 批量写入去重记录。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        conn = connect()
        conn.executemany(
            "INSERT OR IGNORE INTO reminder_log(task_id, log_date, shown_at) VALUES(?,?,?)",
            [(tid, ld, now) for tid, ld in task_ids_logdates],
        )
        conn.commit()


def clear_reminded(task_ids_logdates):
    """删掉去重记录（用户点了"稍后提醒"，到点要能再次弹出）。"""
    with _lock:
        conn = connect()
        conn.executemany(
            "DELETE FROM reminder_log WHERE task_id = ? AND log_date = ?",
            task_ids_logdates,
        )
        conn.commit()


# ---------------- 设置 ----------------

def get_setting(key, default=None):
    with _lock:
        conn = connect()
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    with _lock:
        conn = connect()
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
        conn.commit()
