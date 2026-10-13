# -*- coding: utf-8 -*-
"""视图模型层：把 storage 的行 dict 转成 UI 直接可渲染的 TaskView。

规格 v2.1 §7：
- 零 PyQt import，可纯 pytest 测试；
- 日期运算（eff_date / 距今天数 / 分组 / 连续打卡 / 倒计时文案）全部在这里
  完成，UI 层禁止再算"还有几天"，只渲染；
- explain 文案直接取 reminders.human_explain，UI 不重拼。
"""
from dataclasses import dataclass, field
from datetime import date, timedelta

import storage
from reminders import human_explain
from storage import next_occurrence, RECUR_NONE, STATUS_DONE, STATUS_PENDING

# ---------------- 分组键（§7.3，顺序即中栏自上而下） ----------------
GROUP_OVERDUE = "overdue"
GROUP_TODAY = "today"
GROUP_WEEK = "week"
GROUP_HABIT = "habit"
GROUP_GOAL = "goal"
GROUP_LATER = "later"
GROUP_DONE = "done"

GROUP_ORDER = [GROUP_OVERDUE, GROUP_TODAY, GROUP_WEEK,
               GROUP_HABIT, GROUP_GOAL, GROUP_LATER, GROUP_DONE]
GROUP_LABEL = {
    GROUP_OVERDUE: "逾期",
    GROUP_TODAY: "今天",
    GROUP_WEEK: "7天内",
    GROUP_HABIT: "长期 · 习惯打卡",
    GROUP_GOAL: "长期 · 目标倒计时",
    GROUP_LATER: "普通远期",
    GROUP_DONE: "已完成",
}

# ---------------- 左栏筛选键（§7.4，trae solo 5 项） ----------------
FILTER_ALL = "all"
FILTER_LONG = "long"
FILTER_WEEK = "week"
FILTER_TODAY = "today"
FILTER_DONE = "done"
FILTER_ORDER = [FILTER_ALL, FILTER_LONG, FILTER_WEEK, FILTER_TODAY, FILTER_DONE]

CHECKIN_DAYS = 20  # habit 卡 streak 竖条根数（最近 20 天，§6.2/§8.1.4）


@dataclass(frozen=True)
class TaskView:
    """一张卡片/右栏所需的全部展示数据（§7.2 数据契约）。"""
    id: int
    title: str
    detail: str
    task_type: str                  # 'short' | 'long'
    display_kind: str | None        # 'goal' | 'habit' | None（short）
    status: str                     # pending | done

    # 日期语义（全部在这里算完）
    eff_date: date | None           # next_occurrence(due, recur, today)
    due_time: str | None
    recur: str
    days: int | None                # (eff_date - today).days；None=无日期
    is_overdue: bool
    is_today: bool

    # 展示派生
    group: str                      # 上面的分组键
    badge: tuple | None             # (文案, 语义色键) 如 ("已逾期","urgent")
    explain: str                    # reminders.human_explain 原文
    cd_text: str                    # 倒计时大字："128"/"今天"/"已逾期 1 天"
    cd_unit: str                    # "天" / ""

    # 长期专用
    streak: int = 0
    checkins_20d: list = field(default_factory=list)
    checked_today: bool = False
    journal_count: int = 0
    progress: float | None = None   # goal：已过天数/总天数 0~1；无目标日 None

    # 右栏附加（完成日期/预警展示用）
    completed_at: str | None = None
    lead_days: str = ""


# ---------------- 内部派生 ----------------

def _group_of(task_type: str, display_kind, status: str, days) -> str:
    """互斥优先级（§7.3）：紧急组只收 short；long 永远进 habit/goal。"""
    if status == STATUS_DONE:
        return GROUP_DONE
    if task_type == "long":
        return GROUP_HABIT if display_kind == "habit" else GROUP_GOAL
    if days is None:
        return GROUP_LATER          # 无日期并入「普通远期」置底
    if days < 0:
        return GROUP_OVERDUE
    if days == 0:
        return GROUP_TODAY
    if days <= 7:
        return GROUP_WEEK
    return GROUP_LATER


def _badge_of(task_type: str, status: str, days):
    """紧急徽标：逾期/今天对所有 pending 生效（long 借用 urgent/today 色），
    临期 near 只给 short 的 1~7 天。"""
    if status == STATUS_DONE:
        return None
    if days is not None and days < 0:
        return ("已逾期", "urgent")
    if days == 0:
        return ("今天", "today")
    if task_type != "long" and days is not None and days <= 7:
        return (f"{days}天后", "near")
    return None


def _cd(days):
    """倒计时大字（§7.5 边界：0=今天、-1=已逾期 1 天、None=未设目标日）。"""
    if days is None:
        return ("未设目标日", "")
    if days < 0:
        return (f"已逾期 {-days} 天", "")
    if days == 0:
        return ("今天", "")
    return (str(days), "天")


def _checkin_dates(journals) -> set:
    dates = set()
    for j in journals:
        ts = (j.get("created_at") or "")[:10]
        if len(ts) == 10:
            try:
                dates.add(date.fromisoformat(ts))
            except ValueError:
                pass
    return dates


def _streak(dates: set, today: date) -> int:
    """从今天向昨天回溯连续段；今天没打卡不断链（从昨天起算）。"""
    d = today if today in dates else today - timedelta(days=1)
    n = 0
    while d in dates:
        n += 1
        d -= timedelta(days=1)
    return n


def _progress(task: dict, days, today: date):
    """goal 进度 = 已过天数/总天数（created_at 日期 → due_date），0~1。"""
    if task.get("task_type") != "long" or not task.get("due_date"):
        return None
    try:
        due = date.fromisoformat(task["due_date"])
        created = date.fromisoformat((task.get("created_at") or "")[:10])
    except ValueError:
        return None
    total = (due - created).days
    if total <= 0:
        return 1.0 if (days is not None and days <= 0) else 0.0
    return max(0.0, min(1.0, (today - created).days / total))


def _to_view(task: dict, journals: list, today: date,
             eff_override: date | None = None) -> TaskView:
    due_s = task.get("due_date")
    due = date.fromisoformat(due_s) if due_s else None
    recur = task.get("recur") or RECUR_NONE
    if eff_override is not None:
        eff = eff_override
    elif due:
        eff = next_occurrence(due, recur, today)
    else:
        eff = None
    days = (eff - today).days if eff else None

    task_type = task.get("task_type") or "short"
    display_kind = task.get("display_kind")  # 老库无该列 → None
    if task_type == "long" and display_kind not in ("goal", "habit"):
        display_kind = "goal"                # long 默认目标形态（§6.2）
    status = task.get("status") or STATUS_PENDING

    dates = _checkin_dates(journals)
    cd_text, cd_unit = _cd(days)
    return TaskView(
        id=task["id"],
        title=task.get("title") or "",
        detail=task.get("detail") or "",
        task_type=task_type,
        display_kind=display_kind,
        status=status,
        eff_date=eff,
        due_time=task.get("due_time"),
        recur=recur,
        days=days,
        is_overdue=days is not None and days < 0,
        is_today=days == 0,
        group=_group_of(task_type, display_kind, status, days),
        badge=_badge_of(task_type, status, days),
        explain=human_explain(task, today),
        cd_text=cd_text,
        cd_unit=cd_unit,
        streak=_streak(dates, today),
        checkins_20d=[(today - timedelta(days=CHECKIN_DAYS - 1 - i)) in dates
                      for i in range(CHECKIN_DAYS)],
        checked_today=today in dates,
        journal_count=len(journals),
        progress=_progress(task, days, today),
        completed_at=task.get("completed_at"),
        lead_days=task.get("lead_days") or "",
    )


# ---------------- 构造入口 ----------------

def build_views(today=None) -> list:
    """全量任务视图（含已完成），供中栏分组流使用。"""
    today = today or date.today()
    return [_to_view(t, storage.journals_of(t["id"]), today)
            for t in storage.all_tasks(include_done=True)]


def build_views_for_day(day: date, today=None) -> list:
    """日视图（§7.4/§8.7）：当天任务按 due_time 升序平铺，已完成排最后。

    eff_date 取 day 本身——tasks_on 已按 occurs_on 过滤，卡片表达的是
    "该日在日历上的那一次发生"。
    """
    today = today or date.today()
    views = [_to_view(t, storage.journals_of(t["id"]), today, eff_override=day)
             for t in storage.tasks_on(day, include_done=True)]
    views.sort(key=lambda v: (v.status == STATUS_DONE,
                              v.due_time or "99:99", v.id))
    return views


# ---------------- 分组与筛选 ----------------

_FILTER_GROUPS = {
    FILTER_ALL: GROUP_ORDER,
    FILTER_LONG: [GROUP_HABIT, GROUP_GOAL],
    FILTER_WEEK: [GROUP_OVERDUE, GROUP_TODAY, GROUP_WEEK],
    FILTER_TODAY: [GROUP_TODAY],
    FILTER_DONE: [GROUP_DONE],
}


def _sort_key(group: str, v: TaskView):
    if group in (GROUP_HABIT, GROUP_GOAL):
        # 长期组内：逾期置顶 → 今天 → 未来升序 → 无日期置底（§7.3）
        if v.days is not None and v.days < 0:
            cat = 0
        elif v.days == 0:
            cat = 1
        elif v.days is not None:
            cat = 2
        else:
            cat = 3
        return (cat, v.days if cat == 2 else 0, v.due_time or "99:99", v.id)
    # 紧急/远期组：按有效日期升序，无日期（仅 later）置底
    return (v.eff_date or date.max, v.due_time or "99:99", v.id)


def group_views(views: list, filter_key: str = FILTER_ALL) -> list:
    """按筛选键过滤并分组，返回 [(group_key, [TaskView, ...]), ...]。
    空组整组不出现；done 组按完成时间倒序。"""
    order = _FILTER_GROUPS.get(filter_key, _FILTER_GROUPS[FILTER_ALL])
    buckets = {g: [] for g in order}
    for v in views:
        if v.group in buckets:
            buckets[v.group].append(v)
    out = []
    for g in order:
        items = buckets[g]
        if not items:
            continue
        if g == GROUP_DONE:
            items.sort(key=lambda v: (v.completed_at or "", v.id),
                       reverse=True)
        else:
            items.sort(key=lambda v, g=g: _sort_key(g, v))
        out.append((g, items))
    return out


def filter_counts(views: list) -> dict:
    """左栏 5 项筛选的角标计数（§7.4 口径）。"""
    pending = [v for v in views if v.status != STATUS_DONE]
    return {
        FILTER_ALL: len(pending),
        FILTER_LONG: sum(1 for v in pending if v.task_type == "long"),
        FILTER_WEEK: sum(1 for v in pending if v.task_type != "long"
                         and v.days is not None and v.days <= 7),
        FILTER_TODAY: sum(1 for v in pending if v.task_type != "long"
                          and v.days == 0),
        FILTER_DONE: sum(1 for v in views if v.status == STATUS_DONE),
    }


def has_checkin_today(task_id: int, today=None) -> bool:
    """供 UI 判断"今日已打卡"（§6.2：同日重复点击不插第二条）。"""
    today = today or date.today()
    return today in _checkin_dates(storage.journals_of(task_id))
