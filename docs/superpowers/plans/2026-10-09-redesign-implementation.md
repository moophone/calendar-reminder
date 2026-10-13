# 日历提醒重设计 · 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline) 实现本计划。步骤用 `- [ ]` 勾选跟踪。
> 本项目无 git 仓库：所有"验证"步骤用 `python -m py_compile` / `python -m unittest`，不 commit。

**Goal:** 按两份定稿规格（docs/superpowers/specs/2026-10-09-redesign-design.md 与 docs/AI算法融合参考.md §0）完成 D:\CalendarReminder 的 v6 重设计改造。

**Architecture:** 纯本地离线 PyQt6 桌面应用。新增 countdown.py（纯函数倒计时）、parse_pipeline.py（P2 匹配/P3 终检纯算法层）；storage.py 增量迁移；text_parser.py 升级为 P0 候选枚举；llm_fallback.py 改为"选择器"模式（GBNF+JSONL+分块）；ui.py 主窗口按 v6 镜像布局重写。

**Tech Stack:** Python 3.13.3（标准库 unittest 测试）、PyQt6 6.11.0、SQLite、llama-server.exe（OpenAI 兼容 HTTP + GBNF grammar）。

**权威规格：** 实现与本文冲突时，以两份定稿文档为准；本文只做任务拆解与代码落地。已发现的规格-现实偏差在对应任务内注明。

**关键环境约束（沿用）：**
- Python 字符串内禁止嵌套英文双引号 → 用中文引号""或单引号；
- PowerShell 命令用 `;` 分隔；工作目录 D:\CalendarReminder；
- 真实数据 id=15（SAIS, 2027-03-01）勿删；旧任务迁移后全 short；
- 模型 Qwen3-1.7B-Instruct Q8_0（models/Qwen3-1.7B-Q8_0.gguf），llama/ 目录 b11505；
- 无边框窗口激活：activateWindow() + raise_()。

---

## Task 1: 倒计时函数 + 单测（规格 §3.1）

**Files:**
- Create: `D:\CalendarReminder\countdown.py`
- Create: `D:\CalendarReminder\tests\test_countdown.py`
- Create: `D:\CalendarReminder\tests\__init__.py`（空文件）
- Modify: `D:\CalendarReminder\storage.py`（`_add_months` 改为复用 countdown）

- [ ] **Step 1.1 写失败测试** `tests/test_countdown.py`：

```python
# -*- coding: utf-8 -*-
"""倒计时规格单测（设计文档 §3.1 全部用例）。"""
import unittest
from datetime import datetime

from countdown import countdown, add_months


def dt(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M")


class TestCountdown(unittest.TestCase):
    def test_spec_examples(self):
        self.assertEqual(countdown(dt("2026-10-09 00:00"), dt("2027-03-01 00:00")), "4月20天")
        self.assertEqual(countdown(dt("2026-10-09 00:00"), dt("2027-03-15 00:00")), "5月6天")

    def test_month_end_clamp(self):
        # 2026-01-31 + 1 月 = 2026-02-28（目标月无 31 日取月末）
        self.assertEqual(countdown(dt("2026-01-31 00:00"), dt("2026-02-28 00:00")), "1月0天")

    def test_same_day(self):
        self.assertEqual(countdown(dt("2026-10-09 08:00"), dt("2026-10-09 08:00")), "0天")

    def test_one_day(self):
        self.assertEqual(countdown(dt("2026-10-09 00:00"), dt("2026-10-10 00:00")), "1天")

    def test_week(self):
        self.assertEqual(countdown(dt("2026-10-09 00:00"), dt("2026-10-16 00:00")), "1周0天")
        self.assertEqual(countdown(dt("2026-10-09 00:00"), dt("2026-10-17 00:00")), "1周1天")

    def test_cross_year(self):
        self.assertEqual(countdown(dt("2026-12-31 12:00"), dt("2027-01-01 12:00")), "1天")

    def test_leap_year(self):
        # 2028 闰年：2028-01-31 + 1月 = 2028-02-29
        self.assertEqual(countdown(dt("2028-01-31 00:00"), dt("2028-02-29 00:00")), "1月0天")
        self.assertEqual(countdown(dt("2024-01-31 00:00"), dt("2024-03-01 00:00")), "1月1天")

    def test_hour_granularity(self):
        self.assertEqual(countdown(dt("2026-10-09 08:00"), dt("2026-10-09 20:00")), "12小时")
        self.assertEqual(countdown(dt("2026-10-09 08:00"), dt("2026-10-10 09:00")), "1天1小时")

    def test_add_months(self):
        self.assertEqual(add_months(__import__("datetime").date(2026, 1, 31), 1).isoformat(), "2026-02-28")
        self.assertEqual(add_months(__import__("datetime").date(2024, 1, 31), 1).isoformat(), "2024-02-29")
        self.assertEqual(add_months(__import__("datetime").date(2026, 10, 9), 4).isoformat(), "2027-02-09")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 1.2 跑测试确认失败**：`python -m unittest tests.test_countdown -v` → 预期 `ModuleNotFoundError: countdown`
- [ ] **Step 1.3 实现** `countdown.py`：

```python
# -*- coding: utf-8 -*-
"""倒计时纯函数（设计文档 §3.1 规格）。

量级选择：>=1 月 → "X月X天"；>=1 周 → "X周X天"；>=1 天 → "X天"或"X天X小时"；
<1 天 → "X小时"；同一时刻 → "0天"。
自然月加法：逐月推进保持"日"不变，目标月无该日取月末（01-31 + 1月 = 02-28）。
"""
from datetime import date, datetime, timedelta


def days_in_month(y: int, m: int) -> int:
    if m == 12:
        nxt = date(y + 1, 1, 1)
    else:
        nxt = date(y, m + 1, 1)
    return (nxt - timedelta(days=1)).day


def add_months(d: date, n: int) -> date:
    total = d.year * 12 + (d.month - 1) + n
    y, m = divmod(total, 12)
    m += 1
    return date(y, m, min(d.day, days_in_month(y, m)))


def _add_months_dt(d: datetime, n: int) -> datetime:
    return datetime.combine(add_months(d.date(), n), d.time())


def countdown(start: datetime, end: datetime) -> str:
    """end >= start；end < start 由调用方按逾期处理。"""
    if end <= start:
        return "0天"
    n = 0
    while _add_months_dt(start, n + 1) <= end:
        n += 1
    rem = end - (_add_months_dt(start, n) if n else start)
    days, hours = rem.days, rem.seconds // 3600
    if n >= 1:
        return f"{n}月{days}天"
    weeks, d = divmod(days, 7)
    if weeks >= 1:
        return f"{weeks}周{d}天"
    if days >= 1:
        return f"{days}天{hours}小时" if hours else f"{days}天"
    return f"{hours}小时" if hours else "0天"
```

- [ ] **Step 1.4 跑测试确认通过**：`python -m unittest tests.test_countdown -v` → 全 PASS
- [ ] **Step 1.5 storage 复用**：storage.py 删除 `_add_months`/`_days_in_month` 本体，改为：

```python
from countdown import add_months as _add_months, days_in_month as _days_in_month
```

- [ ] **Step 1.6 回归**：`python -m py_compile storage.py countdown.py; python -m unittest discover -s tests -v` → 全 PASS

## Task 2: storage 迁移（task_type / last_notified_at / 索引 / due_date 可空）+ 单测

**规格偏差注明：** 设计 §3 索引写 `idx_tasks_type_due(task_type, due_date, is_archived)`，但 tasks 表无 `is_archived` 列（完成状态列是 `status`），实际主查询按 `status='pending'` 过滤 → 索引按现实改为 `(status, task_type, due_date)`。

**Files:**
- Modify: `D:\CalendarReminder\storage.py`
- Create: `D:\CalendarReminder\tests\test_storage_migrate.py`

- [ ] **Step 2.1 写失败测试** `tests/test_storage_migrate.py`：

```python
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


class TestMigrate(unittest.TestCase):
    def _old_db(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(OLD_SCHEMA)
        conn.execute("INSERT INTO tasks(title,due_date,created_at) VALUES('SAIS报名','2027-03-01','2026-01-01 00:00:00')")
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
        # 旧数据完好
        row = conn.execute("SELECT * FROM tasks WHERE title='SAIS报名'").fetchone()
        self.assertEqual(row["due_date"], "2027-03-01")
        self.assertEqual(row["task_type"], "short")
        # 新数据允许 NULL 目标日
        conn.execute("INSERT INTO tasks(title,due_date,task_type,created_at) VALUES('长期','x',NULL)")
        # 上行故意写错列数验证报错？不：正确写法↓
        conn.rollback()
        conn.execute("INSERT INTO tasks(title,due_date,task_type,created_at) VALUES('长期',NULL,'long','2026-01-01 00:00:00')")
        conn.commit()
        n = conn.execute("SELECT COUNT(*) c FROM tasks WHERE due_date IS NULL").fetchone()["c"]
        self.assertEqual(n, 1)

    def test_migrate_idempotent(self):
        conn = self._old_db()
        migrate(conn)
        migrate(conn)  # 二次执行不报错不重复
        row = conn.execute("SELECT COUNT(*) c FROM tasks WHERE title='SAIS报名'").fetchone()
        self.assertEqual(row["c"], 1)

    def test_index_exists(self):
        conn = self._old_db()
        migrate(conn)
        idx = conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_tasks_type_due'").fetchone()
        self.assertIsNotNone(idx)


if __name__ == "__main__":
    unittest.main()
```

注意：Step 2.1 代码里 test_migrate_allows_null_due_date 中故意保留两行 INSERT——执行时删掉第一行错误 INSERT 与 rollback（写计划时防止自己抄错，以第二行为准）。

- [ ] **Step 2.2 跑测试确认失败**：`python -m unittest tests.test_storage_migrate -v` → ImportError: migrate
- [ ] **Step 2.3 实现 migrate()**：storage.py 在 `_create_tables` 之后加（connect() 里调用 `migrate(_conn)`）：

```python
def migrate(conn):
    """增量迁移：加 task_type/last_notified_at 列；重建表放开 due_date NOT NULL；
    补右侧面板主查询索引。可重复执行（幂等）。"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
    if not cols:
        return  # 空库（表还没建）
    if "task_type" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN task_type TEXT DEFAULT 'short'")
    if "last_notified_at" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN last_notified_at DATE")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
    ddl = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tasks'").fetchone()[0]
    if "NOT NULL" in " ".join(ddl.upper().split()).replace("DUE_DATE TEXT NOT NULL", "DUE_DATE TEXT NOT NULL|"):
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
                last_notified_at DATE
            );
            INSERT INTO tasks_new(id,title,detail,due_date,due_time,recur,lead_days,
                                  status,created_at,completed_at,task_type,last_notified_at)
                SELECT id,title,detail,due_date,due_time,recur,lead_days,
                       status,created_at,completed_at,task_type,last_notified_at FROM tasks;
            DROP TABLE tasks;
            ALTER TABLE tasks_new RENAME TO tasks;
            """
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tasks_type_due ON tasks(status, task_type, due_date)")
    conn.commit()
```

（DDL 判断简化为 `if "DUE_DATE TEXT NOT NULL" in ddl.upper()`，执行时用这一行判断即可；上面多行 if 只是排版。）

- [ ] **Step 2.4 NULL 兼容改造**（storage.py 内）：
  - `add_task(...)` 签名加 `task_type="short"`，INSERT 列加 task_type；
  - `update_task` allowed 集合加 `"task_type"`；
  - `all_tasks` 排序改 `ORDER BY COALESCE(due_date,'9999-12-31'), COALESCE(due_time,'99:99'), id`；
  - `tasks_on` 循环内开头：`if not t["due_date"]: continue`；
  - `month_has_task` 循环内：`if not t["due_date"]: continue`；
  - `complete_task`：`if row["due_date"] is None: 置 done`（重复任务无日期时兜底直接完成）；
  - 新增 `panel_tasks()`：

```python
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
    today = (today or date.today()).isoformat()
    with _lock:
        conn = connect()
        conn.executemany(
            "UPDATE tasks SET last_notified_at = ? WHERE id = ?",
            [(today, tid) for tid in task_ids])
        conn.commit()


def clear_daily_notified(task_ids):
    """长期任务点"稍后"时清除当日已提醒标记，10 分钟后能再弹。"""
    with _lock:
        conn = connect()
        conn.executemany(
            "UPDATE tasks SET last_notified_at = NULL WHERE id = ?",
            [(tid,) for tid in task_ids])
        conn.commit()
```

- [ ] **Step 2.5 跑测试**：`python -m unittest discover -s tests -v` → 全 PASS
- [ ] **Step 2.6 真库冒烟（保护 id=15）**：`python -c "import storage; storage.connect(); print([t for t in storage.all_tasks(True) if t['id']==15])"` → 确认 SAIS 数据完好且 task_type=short

## Task 3: style.py 暖纸配色（规格 §5）

**Files:**
- Modify: `D:\CalendarReminder\style.py`
- Modify: `D:\CalendarReminder\ui.py`（DotCalendar 圆点色、TaskCard 硬编码色随 QSS 调整，Task 4 一并处理）

- [ ] **Step 3.1 替换色值常量**：

```python
ACCENT = "#e07b39"
ACCENT_DARK = "#c9682b"
DANGER = "#b03434"
WARN = "#b3591c"
BG = "#faf6ef"
CARD = "#fffdf8"
TEXT = "#4a3a24"
TEXT_SUB = "#8b7a5e"
BORDER = "#ecdfc9"
# 长短期徽标（§5 表格）
LONG_BG, LONG_FG = "#f3e8d5", "#8a6a34"    # 长期
NEAR_BG, NEAR_FG = "#fde3c8", "#b3591c"    # 临近
URGENT_BG, URGENT_FG = "#fadadb", "#b03434"  # 24h 内（长期项进入 24h 也用它覆盖）
TODAY_BG = "#fdeeda"                        # 今天高亮底
DONE_DOT = "#c9bda6"                        # 日历已完成圆点（暖灰）
RECUR_DOT = "#57965a"                       # 日历重复任务圆点（暖绿）
```

- [ ] **Step 3.2 QSS 内旧色全量替换**：`#EEF1FE`→`#f7ead9`；`#E3E8FE`→`#fbe3cf`；`#C3C9DC`→`#d8c9ae`；`#D2D7E8`/`#B3BBD6`（滚动条）→`#dccdb2`/`#c4b090`；`selection-color` 保持白/深棕（`{ACCENT_DARK}`）。
- [ ] **Step 3.3 编译冒烟**：`python -m py_compile style.py`；启动 `python main.py` 肉眼确认主窗口换色（此步可延后到 Task 4 完成一起看）。

## Task 4: 主窗口 v6（镜像布局 + 详情卡 + 待办面板 + 折叠 + 空状态 + 日历圆点）

**Files:**
- Modify: `D:\CalendarReminder\ui.py`（MainWindow 全量重写 `_build`/`refresh_day`→`refresh_panel`/交互函数；TaskCard 重构；DotCalendar 圆点逻辑；TaskDialog 加类型；详情卡 ×）

布局规格（§2）：顶栏横跨（标题+＋导入文本/新建/今天/⚙）；下方左右两列：左列固定 292px（日历卡 260×230 固定 + 详情卡占余高），右侧待办面板占余宽。

- [ ] **Step 4.1 DotCalendar 圆点改色**（`_build` 内）：

```python
if all(t["status"] == STATUS_DONE for t in tasks):
    color = QColor(DONE_DOT)
elif d < date.today():
    color = QColor(DANGER)
elif any(t.get("task_type") == "long" for t in tasks):
    color = QColor(LONG_FG)          # 深金 = 长期目标日
elif any((t["due_time"] and _is_within_24h(t, d)) or
         (t["recur"] != RECUR_NONE and t.get("task_type") == "long")
         for t in tasks):
    color = QColor(URGENT_FG)        # 红 = 24 小时内
elif any(t["recur"] != RECUR_NONE for t in tasks):
    color = QColor(RECUR_DOT)        # 暖绿 = 重复
else:
    color = QColor(NEAR_FG)          # 橙 = 短期临近
```

`setMinimumSize(300, 240)` 改 `setFixedSize(260, 230)`。
（`_is_within_24h`：due_date==d 且 due_time 距现在 <24h；实现放 ui.py 模块级小函数。）

- [ ] **Step 4.2 TaskCard 重构**：行布局改为 `check + 标题行(类型徽标+标题+重复) + 副行(倒计时徽标+到期说明)`：
  - 类型徽标：长期→`LONG_BG/LONG_FG`，短期→透明无徽标（视觉降噪）；
  - 倒计时徽标（`countdown_str_for(task)`，详见 Step 4.4）：24h 内→URGENT、临近(≤7d)→NEAR、长期非紧急→LONG、普通→灰底 `#efe6d4`；
  - `clicked` 信号保留；增加选中态样式 `selected` 属性（背景 `TODAY_BG`）。
- [ ] **Step 4.3 倒计时显示函数**（ui.py 模块级）：

```python
def countdown_str_for(task: dict, now=None) -> str:
    """任务 → 复合倒计时文案；无目标日 → 未设目标日；逾期 → 已逾期 X 天。"""
    if not task.get("due_date"):
        return "未设目标日"
    now = now or datetime.now()
    due_d = datetime.strptime(task["due_date"], "%Y-%m-%d").date()
    if task["recur"] != RECUR_NONE:
        eff = next_occurrence(due_d, task["recur"], now.date())
        target = datetime.combine(eff, datetime.min.time())
        if task.get("due_time"):
            h, m = map(int, task["due_time"].split(":"))
            target = target.replace(hour=h, minute=m)
    else:
        target = datetime.combine(due_d, datetime.min.time())
        if task.get("due_time"):
            h, m = map(int, task["due_time"].split(":"))
            target = target.replace(hour=h, minute=m)
    if target < now:
        return f"已逾期 {(now.date() - target.date()).days} 天"
    return countdown(now, target)
```

（import：`from countdown import countdown`；`from storage import next_occurrence`。）

- [ ] **Step 4.4 MainWindow._build 重写**：
  - root 改 QVBoxLayout：顶栏（QHBoxLayout：`日历提醒` appTitle + stretch + ＋导入文本(primary)/新建/今天/⚙）+ body(QHBoxLayout)；
  - 左列 QWidget `setFixedWidth(292)`：卡内 VBox：`self.cal`（260×230 固定）+ 详情卡（stretch=1）；
  - 详情卡：头部行（`d_title` 15px 800 + stretch + `self.btn_collapse` "×" 文本钮）→ 徽标行（`self.d_badges` QLabel，富文本：长期/短期 · 目标日 · 重复规则）→ `self.d_countdown`（22px 800 色 ACCENT_DARK，setToolTip=精确到期时间 `f"精确到期：{due_date} {due_time or '全天'}"`）→ `d_explain`（human_explain）→ `d_detail`（QTextEdit readonly 无边框，stretch）→ `d_actions`（✓完成/编辑/删除）→ journals 列表 + 输入行；
  - 空状态（`_show_placeholder`）：`d_title`="暂无待办"、`d_detail`="点右上角 ＋导入文本 开始"、隐藏 d_actions/d_journals/j_input/btn_collapse；
  - 右列：标题行 `待办` + `self.count_lab`（`共 N 条 · 长期 x · 短期窗口 y`）；`self.panel = QScrollArea()`（widgetResizable，内含 `self.panel_body` VBox）：长期节头 `— 长期 · 常驻 —` + 卡片 + （>5 条时）`展开全部 N 条` 链接钮；短期节头 `— 短期 · 7 天内到期 —` + 卡片；两节头样式 TEXT_SUB 12px；
  - `self._long_expanded = False` 状态；点击切换重建面板。
- [ ] **Step 4.5 refresh_panel**（替代 refresh_day）：

```python
def refresh_panel(self):
    tasks = storage.panel_tasks()
    longs = [t for t in tasks if t.get("task_type") == "long"]
    shorts = [t for t in tasks if t.get("task_type") != "long"]
    self.count_lab.setText(
        f"共 {len(tasks)} 条 · 长期 {len(longs)} · 短期窗口 {len(shorts)}")
    # 重建面板卡（长期折叠 5 条），点行 → select_task；点面板空白 → 回落自动选中
    ...
    # 自动选中最近到期项（不留空白）
    if not self.selected_task_id or not storage.get_task(self.selected_task_id):
        self.selected_task_id = tasks[0]["id"] if tasks else None
    t = storage.get_task(self.selected_task_id) if self.selected_task_id else None
    self._show_detail(t) if t else self._show_placeholder()
    self.cal.invalidate()
    self.data_changed.emit()
```

`refresh_all` 内 `refresh_day()` 改 `refresh_panel()`；main.py `show_window` 里 `refresh_day` 调用同步改。
- [ ] **Step 4.6 交互**：
  - `select_task(id)`：设置 selected_task_id + 刷新选中样式 + `_show_detail`；再点同行保持选中；
  - 面板空白点击（panel_body 的 mousePressEvent）→ `self.selected_task_id = None; refresh_panel()`（回落自动选中最近到期）；
  - `btn_collapse` → 同上（× 收起 = 回落自动选中）；
  - `Esc`：MainWindow.keyPressEvent → 同上收起；
  - 日历点击日期 `on_pick_date`：设 `self.highlight_day`，重建面板时当天到期行背景 TODAY_BG（轻高亮，不切换数据）；再点同日取消高亮；
  - `edit_selected`：TaskDialog 支持改 task_type（Step 4.7）。
- [ ] **Step 4.7 TaskDialog 加类型**：
  - 顶部加 `self.task_type = QComboBox()`，项：短期(short)/长期(long)，默认 short；
  - 加 `self.no_due = QCheckBox("无目标日（仅长期）")`；勾选 → date_edit 禁用；仅当 task_type=long 时可用；
  - `get_data()` 返回 `task_type`；无目标日时 `due_date=None, due_time=None`；
  - 编辑回填：`task["task_type"]`、`task["due_date"] is None` → 勾上 no_due；
  - storage.update_task 已允许 task_type/due_date（Task 2）。
- [ ] **Step 4.8 编译+启动冒烟**：`python -m py_compile ui.py storage.py main.py; python main.py` 手查：布局、点行联动、× 收起、空状态、折叠、日历圆点色、新建可设长期。
- [ ] **Step 4.9 真库回归**：确认 id=15 显示正常、右侧面板出现（旧任务全 short，若 due 在 7 天外则只在日历/编辑里可见——属预期）。

## Task 5: 导入管线 P0 —— text_parser 候选枚举 + 单测

**Files:**
- Modify: `D:\CalendarReminder\text_parser.py`
- Create: `D:\CalendarReminder\tests\test_p0.py`

- [ ] **Step 5.1 数据结构**（text_parser.py 顶部）：

```python
@dataclass
class Sentence:
    sid: str      # "S1"
    text: str

@dataclass
class Candidate:
    cid: str      # "D1"/"T1"/"R1"
    raw: str      # 原文片段
    value: str    # ISO 日期 / HH:MM / recur key
    ambiguous: bool = False

@dataclass
class PipelineResult:
    sentences: list            # list[Sentence]
    date_cands: list           # list[Candidate]
    time_cands: list
    recur_cands: list
    events: list               # list[ParsedEvent]，带 source_sentences
```

ParsedEvent 加字段 `source_sentences: list = field(default_factory=list)`。
- [ ] **Step 5.2 句子切分 + 短句合并**：

```python
_CUE_RE = re.compile(r"\d|[今明后昨][天日]|周[一二三四五六日天]|每月|每周|每天|月底|次年|下个?月|每年")

def split_sentences(text: str) -> list[Sentence]:
    """换行/句末标点切分 → 短句合并（<10 字且无时间线索向上并）→ 编号 S1..Sn。"""
    raw = split_events(text)
    merged: list[str] = []
    for s in raw:
        if merged and len(s) < 10 and not _CUE_RE.search(s):
            merged[-1] += s          # 碎片并入上一句
        else:
            merged.append(s)
    return [Sentence(f"S{i+1}", s) for i, s in enumerate(merged)]
```

- [ ] **Step 5.3 全文候选扫描**（新函数，复用现有正则逻辑改为 finditer 枚举全部命中）：
  - `_scan_dates(text, today)` → list[(raw, value, ambiguous)]：依次扫 完整年月日 / X月X日 / X月底 / N天后 / 相对词(今天明天后天大后天) / 周X(带本下下下前缀) / X月（无日→`_guess_year(month,1,today)` 的 1 号，`ambiguous=True`）；同一 (raw,value) 去重；
  - `_scan_times(text)` → [(raw,"HH:MM")]（复用 `_extract_time` 的正则，finditer 全枚举）；
  - `_scan_recurs(text, today)` → [(raw,recur)]（每年X月X日/每月X号/每周X/每天）。
- [ ] **Step 5.4 P0 主入口**：

```python
def parse_pipeline(text: str, today: date | None = None) -> PipelineResult:
    today = today or date.today()
    sents = split_sentences(text)
    body = "\n".join(s.text for s in sents)
    date_cands = [Candidate(f"D{i+1}", *c) for i, c in enumerate(_scan_dates(body, today))]
    time_cands = [Candidate(f"T{i+1}", *c) for i, c in enumerate(_scan_times(body))]
    recur_cands = [Candidate(f"R{i+1}", *c) for i, c in enumerate(_scan_recurs(body, today))]
    sid_of = {s.text: s.sid for s in sents}
    events = []
    for s in sents:
        ev = parse_line(s.text, today)
        ev.source_sentences = [sid_of[s.text]]
        events.append(ev)
    # 规则双模式择优沿用现有判断（编号分组→分组事件合并各自 source_sentences）
    grouped = parse_grouped(text, today)
    if grouped is not None:
        chunks = split_events(text)
        cues = sum(1 for ev in events if ev.has_cue)
        if len(chunks) >= 2 and cues <= 1 and looks_like_heading(chunks[0]):
            events = grouped  # 整段合并模式：source_sentences=全部
            for ev in events:
                ev.source_sentences = [s.sid for s in sents]
    return PipelineResult(sents, date_cands, time_cands, recur_cands, events)
```

（逐行模式下事件 1 句 1 条维持现状；分组模式每条 source_sentences=该节覆盖的句子——执行时按节文本包含的句子映射。）
- [ ] **Step 5.5 单测** `tests/test_p0.py`：
  - 两赛事样例文本（含"SAIS 大赛报名截止时间为次年3月1日"）→ `parse_pipeline` events≥2、date_cands 含 value="2027-03-01"、句子编号连续；
  - "10号交材料" → 候选含 ambiguous=True；
  - 碎片句合并："报名方式：官网 x.cn" 并入前句；
  - 每条事件 source_sentences 非空。
- [ ] **Step 5.6 跑测试**：`python -m unittest tests.test_p0 -v` → PASS；`python -m unittest discover -s tests -v` 回归。

## Task 6: llm_fallback 选择器模式（GBNF+探针+分块+JSONL）+ parse_pipeline P2/P3

**Files:**
- Modify: `D:\CalendarReminder\llm_fallback.py`
- Create: `D:\CalendarReminder\parse_pipeline.py`（P2 匹配 + P3 终检 + 日志）
- Create: `D:\CalendarReminder\tests\test_p2.py`

- [ ] **Step 6.1 _chat 支持 grammar/temperature**：签名 `_chat(messages, max_tokens=300, grammar=None, temperature=0.0)`，body 加 `"grammar": grammar`（None 时不带该键）、`"temperature": temperature`。
- [ ] **Step 6.2 GBNF 生成**：

```python
def build_grammar(n_dates: int, n_times: int, n_recurs: int) -> str:
    dids = ' | '.join([f'"\\"D{i}\\""' for i in range(1, n_dates + 1)] + ['"\\"null\\""']) or '"null"'
    tids = ' | '.join([f'"\\"T{i}\\""' for i in range(1, n_times + 1)] + ['"\\"null\\""']) or '"null"'
    rids = ' | '.join([f'"\\"R{i}\\""' for i in range(1, n_recurs + 1)] + ['"\\"null\\""']) or '"null"'
    return f'''
root ::= line (("\\n" | "\\r\\n") line)*
line ::= "{{" ws "\\"sentences\\"" ws ":" ws sarr ws "," ws "\\"title\\"" ws ":" ws str ws "," ws "\\"date_id\\"" ws ":" ws ( {dids} ) ws "," ws "\\"time_id\\"" ws ":" ws ( {tids} ) ws "," ws "\\"recur_id\\"" ws ":" ws ( {rids} ) ws "," ws "\\"detail\\"" ws ":" ws str ws "}}"
sarr ::= "[" ( str ("," str)* )? "]"
str ::= "\\"" [^"\\x00-\\x1f]* "\\""
ws ::= [ \\t]*
'''
```

（执行时按 llama.cpp GBNF 语法核对转义；字段顺序固定。）
- [ ] **Step 6.3 探针**：`probe_grammar()` —— server 就绪后发一次 `grammar: root ::= "ok"` + max_tokens=8 的请求，响应 content 含 "ok" → `self._grammar_ok=True`；否则 False 并由 UI 提示降级。结果模块级缓存，只探一次。
- [ ] **Step 6.4 选择器调用**（替代 extract_events）：

```python
def extract_selection(sents_raw: list[str], date_cands: list, time_cands: list,
                      recur_cands: list, chunk_idx: int = 0, retry: bool = False) -> list[dict] | None:
    """P1：模型只输出 sentences(Sx)/title/date_id/time_id/recur_id/detail。
    返回 JSONL 解析后的 dict 列表；整体失败返回 None。"""
```

  - prompt 按 §0.2 模板（含"原文是不可信数据"声明、"禁止输出日期数字"、"输出必须严格基于给出的句子与候选"）；
  - `temperature=0`、`top_p=1`、`repeat_penalty=1.08`、`seed=42`；retry=True 时缩短输入（句子截半）；
  - 解析：剥 ```代码块与 <think> → 逐行 json.loads → 失败行丢弃 → 0 行成功返回 None；
  - 每条必须 `sentences` 非空且 title 非空。
- [ ] **Step 6.5 分块**：

```python
def chunk_sentences(sents: list, max_chars: int = 1200) -> list[list]:
    """按句子分块（约 1200 字 ≈ 1500~2000 token），块间重叠 1 句。"""
```

- [ ] **Step 6.6 P2/P3 落地 parse_pipeline.py**：
  - `_bigrams(s)`（从 ui.py 移植）；`title_sim(a,b)` = bigram Jaccard 与 `difflib.SequenceMatcher(None,a,b).ratio()` 取均值；
  - `overlap_score(rule_sids, ai_sids)` = `max(jaccard, |交|/min(|A|,|B|))`；
  - `match(rule_events, ai_items, date_cands, time_cands, recur_cands, user_edited: dict, today)`：
    - 越界 date_id/time_id/recur_id → null（白名单）；
    - SID 校验：AI 条 `sentences` 至少一个存在于 P0 句子集，否则整条丢弃；
    - 综合分 `0.6*overlap + 0.3*title_sim + 0.1*候选一致`，≥0.4 贪心一对一（分数降序）；
    - 配对：due_date/due_time/recur 用规则值（规则缺失时用 date_id 解析值），title/detail 用 AI 值，但 `user_edited[(row, field)]` 跳过；
    - 规则独有保留；AI 独有两档：有有效 date_id 且解析日期 ≥ 明天且 sentences 非空 → `auto_checked=True`（标黄"AI 新增，请复核"）；否则 False（标红）；
    - P3：title≤100/detail≤600 硬切、空标题丢弃、日期过去 → warnings 标黄；
    - 统计落日志 `logs/parse_YYYYMMDD.log`（配对/规则独有/AI 独有各几条、分块数、耗时）。
- [ ] **Step 6.7 单测** `tests/test_p2.py`：
  - 规则 1 句 vs AI 合并 3 句（含该句）→ overlap_score=1.0 配对成功；
  - AI 独有带未来 date_id → auto_checked=True；无 date_id → False；
  - user_edited={"title"} → AI 标题不覆盖；
  - 越界 S99 → 整条丢弃；
  - SAIS 候选 D1="2027-03-01" → AI 选 D1 → 合并结果 due_date="2027-03-01"。
- [ ] **Step 6.8 跑测试**：`python -m unittest discover -s tests -v` → 全 PASS。

## Task 7: ImportDialog 重构（智能解析 + 取消 + 类型列 + 两档勾选 + 用户编辑优先 + 预加载）

**Files:**
- Modify: `D:\CalendarReminder\ui.py`（ImportDialog 重写；_AiSelectWorker 新增；删 _AiDateWorker/_AiExtractWorker/_match_anchor/_bigrams 死代码）
- Modify: `D:\CalendarReminder\main.py`（预加载线程 + 长期每日提醒接线）
- Modify: `D:\CalendarReminder\llm_fallback.py`（preload）

- [ ] **Step 7.1 llm_fallback.preload()**：

```python
def preload():
    """软件启动后低优先级预启动 llama-server，消除首次点击约 5 秒等待。"""
    if not is_available():
        return
    def _go():
        try:
            time.sleep(2)
            _ensure_server()
            probe_grammar()
        except Exception:
            pass
    threading.Thread(target=_go, daemon=True, name="llama-preload").start()
```

（`import threading` 加到头部。main.py `CalendarApp.__init__` 末尾调 `llm_fallback.preload()`。）
- [ ] **Step 7.2 _AiSelectWorker**（QThread）：

```python
class _AiSelectWorker(QThread):
    progress = pyqtSignal(int, int)        # 已完成块, 总块数
    chunk_done = pyqtSignal(int, list)     # 块序号, AI 行列表
    finished_all = pyqtSignal(bool)        # 是否整体成功

    def __init__(self, pipeline, parent=None): ...
    def cancel(self): self._cancel = True
    def run(self):
        # 逐块 extract_selection → chunk_done；中途 _cancel → 发 finished_all(False) 返回
```

- [ ] **Step 7.3 ImportDialog 重写**：
  - 删 4 个 QRadioButton/mode_grid/QButtonGroup、_pick_events、_start_ai_extract/_on_ai_extracted/_run_ai/_on_ai_one/_on_ai_done；
  - 保留：tip 说明、强制绑定日期行（兜底无日期文本）、输入框、表格、取消/确认导入；
  - `parse_btn` 文案"智能解析"→ 点击 `do_parse()`：`parse_pipeline(text)` → P0 结果上表（毫秒级）→ 记录 `self._pipeline` → 若 `llm_fallback.is_available()` 启动 `_AiSelectWorker`；运行中按钮变"取消 AI"，再点 → `worker.cancel()`；
  - 表格 6 列：`导入/日期/时间/重复/类型/事项`；"类型"列 QComboBox（短期/长期）；
  - `user_edited: set[(row, field)]`：date/time/recur 控件 change 信号 + title itemChanged 时记录（AI 合并时跳过）；
  - `_on_chunk_done`：收齐所有块 → `match(...)` → 原地更新表格（配对行更新 title/detail/日期缺失补值，变化单元格 setBackground(NEAR_BG) 且 QTimer.singleShot(2000) 恢复）；AI 独有行追加（两档：黄底自动勾选 / 红底不勾选）；状态栏 `AI 已优化 N 处（跳过用户已改 M 处）`；
  - 分块 >1 时状态栏显示 `AI 解析中 x/y`（progress 信号）；
  - 表格顶部按钮行加"全选/全不选"（toggle 全部勾选框）；
  - `accepted_events()` 加 `task_type`（读类型列）与 AI 独有行 detail；
  - AI 整体失败 → 状态栏"AI 解析失败，已使用规则结果"，表格保持 P0。
- [ ] **Step 7.4 MainWindow.open_import**：accepted_events 带 task_type → `storage.add_task(**ev)` 兼容（Task 2 已加参数）。
- [ ] **Step 7.5 冒烟**：粘贴两赛事文本 → 规则秒出 → AI 接管标题升级、日期不变；无模型环境（改 models 目录名）→ 纯规则不报错。

## Task 8: 提醒改造（长期每日一次 + Popup × + Esc + 屏幕定位）+ 验收

**Files:**
- Modify: `D:\CalendarReminder\reminders.py`
- Modify: `D:\CalendarReminder\ui.py`（ReminderPopup）
- Modify: `D:\CalendarReminder\main.py`

- [ ] **Step 8.1 reminders.py**：
  - `KIND_LONG = "long"`、KIND_LABEL["长期目标"]、BADGE_QSS 加 long 项（ui.py 内字典）；
  - `collect_due`：循环内 `if t.get("task_type") == "long" or not t["due_date"]: continue`（长期走每日通道；无目标日不提醒）；
  - `human_explain`：`if not task.get("due_date"): return "长期 · 未设目标日"`；
  - `today_brief`：无目标日不计；
  - 新增：

```python
def collect_long_daily(today=None) -> list[ReminderItem]:
    """长期任务每日启动提醒：last_notified_at < today 的有目标日长期项。"""
    today = today or date.today()
    out = []
    for t in storage.long_tasks_for_daily(today):
        due = datetime.strptime(t["due_date"], "%Y-%m-%d").date()
        diff = (due - today).days
        out.append(ReminderItem(task=t, eff_date=due, kind=KIND_LONG, days=diff))
    out.sort(key=lambda i: i.eff_date)
    return out
```

- [ ] **Step 8.2 ReminderEngine**：加 `check_long_daily(force=False)`：过滤 `_snoozed_until` → `items_ready.emit` + `storage.mark_daily_notified([ids])`；`snooze` 对长期项额外 `storage.clear_daily_notified([ids])`（稍后 10 分钟能再弹，同日重启不弹——last_notified_at 已被 clear，重启会再弹一次。两难：clear 后重启会弹。取舍：**snooze 不 clear last_notified_at**，仅内存 snooze（10 分钟内 force 检查时再弹）；同日重启不弹，严格符合"每天首次启动弹一次"）。
  → 最终：snooze 长期项不写库，`check_long_daily(force=True)`（悬浮球"立即检查提醒"）时忽略 last_notified_at 但仍受内存 snooze 约束。
- [ ] **Step 8.3 main.py 接线**：启动 `QTimer.singleShot(900, ...)` 里加 `engine.check_long_daily(force=True)`；`_wire` 中 `ball.check_requested` 追加 `check_long_daily(force=True)`。
- [ ] **Step 8.4 ReminderPopup**：
  - 标题行右端加 `×` 按钮（fixedWidth 28）→ `self.close()`（等效"知道了"）；
  - `keyPressEvent`：Esc → close；
  - 定位改 `screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen(); g = screen.availableGeometry()`（import QCursor）；
  - 已有 flags `Tool|Frameless|StaysOnTop` 保持；show 后 `activateWindow()+raise_()`（main.py 已做）；
  - BADGE_QSS 加 `KIND_LONG: (LONG_FG, "长期")`。
- [ ] **Step 8.5 全局退出规范自查（§6）**：
  - TaskDialog/ImportDialog：QDialog 默认 Esc=reject ✓；`ok.setDefault(True)` 实现 Enter=保存；
  - 详情卡 btn_collapse ✓（Task 4）；ReminderPopup ×/Esc ✓；
  - 无边框 FloatBall 右键菜单含"退出" ✓（已有）。
- [ ] **Step 8.6 验收清单逐项自查（§8）**：手动过一遍设计 §8 全部 12 项，逐项记录通过/偏差；重点：
  - `python -m unittest discover -s tests -v` 全绿；
  - `python main.py`：布局/联动/空状态/折叠/圆点色/取消 AI/两档勾选/全选按钮；
  - 长期任务同日二次启动不重复弹（改 last_notified_at 验证）；
  - id=15 完好：`python -c "import storage; print(storage.get_task(15))"`；
  - 模型缺失退化：临时改 models 目录名启动 → 无报错、AI 入口隐藏。
- [ ] **Step 8.7 收尾汇报**：列出全部规格偏差（索引列名、snooze 语义等）与验证证据。

---

## 任务依赖与顺序

Task 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8（串行；1/2/3 无 UI 依赖可先做，4 依赖 2/3，7 依赖 5/6，8 收尾）。

## 规格偏差登记（实现时如再发现，记此处并在完成汇报列明）

1. 索引列 `(status, task_type, due_date)` 替代文档的 `(task_type, due_date, is_archived)`——tasks 表无 is_archived 列；
2. 长期任务"稍后 10 分钟"仅内存 snooze，不回写 last_notified_at（保证"同日多次启动只弹一次"规格不被 snooze 破坏）；
3. Task 6：`extract_selection(sents)` 接受 Sentence 对象列表而非 SID 字符串列表——重试截半时保留全局 SID，避免与 P0 候选/句子编号错位；
4. Task 6：`match()` 增加 `valid_sids` 参数（P0 全部句子 SID 集合），P3 终检的 SID 校验按此集合判定，而非从事件 source_sentences 推断；
5. Task 6：GBNF `str` 规则允许转义序列（`"\\" .` 分支），否则模型输出含 `\"`/`\n` 的 detail 会被语法截断；
6. Task 6：AI 独有条目"自动勾选"档在规格条件（date_id 有效+日期在未来+sentences 非空）上追加"非 ambiguous"——歧义日期（如"10号"）自动勾选易误导；
7. Task 7：`user_edited` 接口定为 `{(行号, 字段)}` 集合、按行聚合后传给 match（规格只说"每行 set[field]"，行号承载靠实现约定，现明确）。
