# -*- coding: utf-8 -*-
"""提醒引擎：后台 QTimer 每分钟检查，产出待提醒事项；弹窗完全交给主线程。

设计要点（吸取同类项目教训）：
- 网络零依赖，启动零阻塞，开机即可交互；
- 后台只收集数据并发信号，不直接碰 UI，弹窗在主线程非模态展示；
- 同一天同一周期实例只提醒一次（reminder_log 去重）；
- "稍后提醒"只暂存内存（重启后重新提醒，反而符合"怕忘"诉求）。
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

import storage
from storage import next_occurrence, STATUS_PENDING, RECUR_NONE

KIND_TODAY = "today"
KIND_OVERDUE = "overdue"
KIND_LEAD = "lead"
KIND_RECUR_TODAY = "recur_today"
KIND_LONG = "long"

KIND_LABEL = {
    KIND_TODAY: "今天到期",
    KIND_OVERDUE: "已逾期",
    KIND_LEAD: "即将到期",
    KIND_RECUR_TODAY: "今日重复",
    KIND_LONG: "长期目标",
}

KIND_STYLE = {
    KIND_TODAY: "badge-today",
    KIND_OVERDUE: "badge-overdue",
    KIND_LEAD: "badge-lead",
    KIND_RECUR_TODAY: "badge-recur",
    KIND_LONG: "badge-long",
}


@dataclass
class ReminderItem:
    task: dict
    eff_date: date           # 本次提醒针对的周期实例日期
    kind: str
    days: int                # 距今天数（逾期为负）

    @property
    def log_key(self):
        return self.task["id"], self.eff_date.isoformat()


def human_explain(task: dict, today: date | None = None) -> str:
    """给单条任务生成"人话解释"，主界面和提醒弹窗共用。"""
    today = today or date.today()
    if not task.get("due_date"):
        type_text = "长期" if task.get("task_type") == "long" else ""
        return f"{type_text} · 未设目标日".strip(" ·")
    due = datetime.strptime(task["due_date"], "%Y-%m-%d").date()
    eff = next_occurrence(due, task["recur"], today)
    diff = (eff - today).days

    recur_text = storage.RECUR_LABEL.get(task["recur"], "")
    time_text = f" {task['due_time']}" if task.get("due_time") else ""

    if task["status"] != STATUS_PENDING:
        return f"已完成 · 锚点日期 {eff:%m月%d日}{time_text}"

    if task["recur"] != RECUR_NONE:
        if diff == 0:
            tail = "今天就轮到"
        elif diff == 1:
            tail = "明天轮到"
        else:
            tail = f"{diff} 天后轮到（{eff:%m月%d日}）"
        return f"{recur_text}重复 · {tail}{time_text}"

    if diff < 0:
        tail = f"已逾期 {-diff} 天（{eff:%m月%d日}到期）"
    elif diff == 0:
        tail = "今天到期"
    elif diff == 1:
        tail = "明天到期"
    else:
        tail = f"还有 {diff} 天到期（{eff:%m月%d日}）"

    leads = [x.strip() for x in (task.get("lead_days") or "").split(",") if x.strip()]
    lead_text = f" · 提前 {'/'.join(leads)} 天预警" if leads else ""
    return tail + time_text + lead_text


def collect_due(today=None) -> list[ReminderItem]:
    """算出当前需要进弹窗的所有事项。"""
    today = today or date.today()
    items = []
    for t in storage.all_tasks(include_done=False):
        if t.get("task_type") == "long" or not t["due_date"]:
            # 长期任务走"每日启动提醒"通道；无目标日任务不提醒
            continue
        due = datetime.strptime(t["due_date"], "%Y-%m-%d").date()
        eff = next_occurrence(due, t["recur"], today)
        diff = (eff - today).days
        kind = None

        if t["recur"] != RECUR_NONE:
            if diff == 0:
                kind = KIND_RECUR_TODAY
        else:
            if diff == 0:
                kind = KIND_TODAY
            elif diff < 0:
                kind = KIND_OVERDUE
            else:
                leads = []
                for x in (t.get("lead_days") or "").split(","):
                    x = x.strip()
                    if x.isdigit():
                        leads.append(int(x))
                if diff in leads:
                    kind = KIND_LEAD

        if kind:
            items.append(ReminderItem(task=t, eff_date=eff, kind=kind, days=diff))

    # 逾期 > 今天 > 预警，同类按时间排
    order = {KIND_OVERDUE: 0, KIND_TODAY: 1,
             KIND_RECUR_TODAY: 2, KIND_LEAD: 3}
    items.sort(key=lambda i: (order[i.kind], i.eff_date,
                              i.task.get("due_time") or "99:99"))
    return items


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


def today_brief(today=None) -> tuple[int, int]:
    """返回 (今天要办的件数, 逾期未完成件数)，供悬浮球角标使用。"""
    today = today or date.today()
    n_today = 0
    n_overdue = 0
    for t in storage.all_tasks(include_done=False):
        if not t["due_date"]:
            continue  # 无目标日不计入今天/逾期统计
        due = datetime.strptime(t["due_date"], "%Y-%m-%d").date()
        eff = next_occurrence(due, t["recur"], today)
        diff = (eff - today).days
        if diff == 0:
            n_today += 1
        elif diff < 0 and t["recur"] == RECUR_NONE:
            n_overdue += 1
    return n_today, n_overdue


class ReminderEngine(QObject):
    """定时检查 → 过滤已提醒/已稍后 → 发信号给主线程弹窗。"""

    items_ready = pyqtSignal(list)  # list[ReminderItem]
    ticked = pyqtSignal()           # 每分钟心跳，供界面刷新角标/跨天

    def __init__(self, parent=None):
        super().__init__(parent)
        self._snoozed_until: dict[int, datetime] = {}
        self._timer = QTimer(self)
        self._timer.setInterval(60_000)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start()

    def _on_tick(self):
        self.check()
        self.ticked.emit()

    def check(self, force: bool = False):
        items = collect_due()
        now = datetime.now()
        due_items = []
        fresh_logs = []
        for it in items:
            tid, log_date = it.log_key
            if not force and storage.already_reminded(tid, log_date):
                continue
            until = self._snoozed_until.get(tid)
            if until and now < until:
                continue
            due_items.append(it)
            fresh_logs.append((tid, log_date))
        if due_items:
            # 发信号前先登记，避免 60 秒轮询与开机首轮重复弹窗
            storage.mark_reminded(fresh_logs)
            self.items_ready.emit(due_items)

    def check_long_daily(self, force: bool = False):
        """长期任务每日通道：每天首次启动弹一次。
        force（悬浮球"立即检查提醒"）忽略 last_notified_at，仍受内存 snooze 约束。"""
        if force:
            tasks = [t for t in storage.all_tasks(include_done=False)
                     if t.get("task_type") == "long" and t.get("due_date")]
        else:
            tasks = storage.long_tasks_for_daily(date.today())
        now = datetime.now()
        due_items = []
        for t in tasks:
            due = datetime.strptime(t["due_date"], "%Y-%m-%d").date()
            it = ReminderItem(task=t, eff_date=due, kind=KIND_LONG,
                              days=(due - date.today()).days)
            until = self._snoozed_until.get(t["id"])
            if until and now < until:
                continue
            due_items.append(it)
        due_items.sort(key=lambda i: i.eff_date)
        if due_items:
            storage.mark_daily_notified([it.task["id"] for it in due_items])
            self.items_ready.emit(due_items)

    def snooze(self, items: list[ReminderItem], minutes: int = 10):
        until = datetime.now() + timedelta(minutes=minutes)
        for it in items:
            self._snoozed_until[it.task["id"]] = until
