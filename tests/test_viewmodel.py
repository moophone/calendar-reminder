# -*- coding: utf-8 -*-
"""viewmodel 单测（规格 v2.1 §7.5）：分组、重复归一、streak、cd_text 边界。

用独立临时库隔离：monkeypatch storage.DB_PATH 并重置连接缓存。
"""
import sqlite3
from datetime import date, timedelta

import pytest

import storage
import viewmodel as vm

TODAY = date.today()


@pytest.fixture()
def db(monkeypatch, tmp_path):
    monkeypatch.setattr(storage, "DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setattr(storage, "_conn", None)
    storage.connect()
    yield
    storage.connect().close()
    monkeypatch.setattr(storage, "_conn", None)


def _add(title, due=None, task_type="short", recur="none", status="pending",
         due_time=None, created=None):
    tid = storage.add_task(title, due.isoformat() if due else None,
                           due_time=due_time, recur=recur, status=status,
                           task_type=task_type)
    if created:  # 覆盖 created_at 以测 progress
        conn = storage.connect()
        conn.execute("UPDATE tasks SET created_at=? WHERE id=?",
                     (created, tid))
        conn.commit()
    return tid


def _add_display_kind_column():
    """模拟 P2 之后的库结构。"""
    conn = storage.connect()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
    if "display_kind" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN display_kind TEXT")
        conn.commit()


def _set_display_kind(tid, kind):
    conn = storage.connect()
    conn.execute("UPDATE tasks SET display_kind=? WHERE id=?", (kind, tid))
    conn.commit()


def _journal_on(tid, day, n=1):
    conn = storage.connect()
    for i in range(n):
        conn.execute(
            "INSERT INTO journals(task_id, content, created_at) VALUES(?,?,?)",
            (tid, "打卡", f"{day.isoformat()} 08:0{i}:00"))
    conn.commit()


def _view_of(views, tid):
    return next(v for v in views if v.id == tid)


# ---------------- 分组（§7.5-1） ----------------

def test_group_assignment(db):
    t_over = _add("昨天", due=TODAY - timedelta(days=1))
    t_today = _add("今天", due=TODAY)
    t_week = _add("三天后", due=TODAY + timedelta(days=3))
    t_later = _add("十天后", due=TODAY + timedelta(days=10))
    t_nodate = _add("无日期", due=None)
    t_done = _add("已完成", due=TODAY, status="done")
    t_long = _add("长期目标", due=TODAY + timedelta(days=30),
                  task_type="long")
    views = vm.build_views()
    assert _view_of(views, t_over).group == vm.GROUP_OVERDUE
    assert _view_of(views, t_today).group == vm.GROUP_TODAY
    assert _view_of(views, t_week).group == vm.GROUP_WEEK
    assert _view_of(views, t_later).group == vm.GROUP_LATER
    assert _view_of(views, t_nodate).group == vm.GROUP_LATER  # 置底并入
    assert _view_of(views, t_done).group == vm.GROUP_DONE
    assert _view_of(views, t_long).group == vm.GROUP_GOAL


def test_group_order_and_empty_groups(db):
    _add("十天后", due=TODAY + timedelta(days=10))
    groups = vm.group_views(vm.build_views())
    keys = [g for g, _ in groups]
    assert keys == [vm.GROUP_LATER]  # 空组整组不出现


def test_group_views_filter(db):
    _add("昨天", due=TODAY - timedelta(days=1))
    _add("今天", due=TODAY)
    _add("长期", due=TODAY + timedelta(days=30), task_type="long")
    _add("已完成", due=TODAY, status="done")
    views = vm.build_views()
    assert [g for g, _ in vm.group_views(views, vm.FILTER_WEEK)] == [
        vm.GROUP_OVERDUE, vm.GROUP_TODAY]
    assert [g for g, _ in vm.group_views(views, vm.FILTER_TODAY)] == [
        vm.GROUP_TODAY]
    assert [g for g, _ in vm.group_views(views, vm.FILTER_LONG)] == [
        vm.GROUP_GOAL]
    assert [g for g, _ in vm.group_views(views, vm.FILTER_DONE)] == [
        vm.GROUP_DONE]
    assert [g for g, _ in vm.group_views(views, vm.FILTER_ALL)] == [
        vm.GROUP_OVERDUE, vm.GROUP_TODAY, vm.GROUP_GOAL, vm.GROUP_DONE]


def test_later_group_nodate_last(db):
    t1 = _add("无日期", due=None)
    t2 = _add("三十天后", due=TODAY + timedelta(days=30))
    t3 = _add("十天后", due=TODAY + timedelta(days=10))
    groups = dict(vm.group_views(vm.build_views()))
    ids = [v.id for v in groups[vm.GROUP_LATER]]
    assert ids == [t3, t2, t1]  # 日期升序，无日期置底


# ---------------- 重复任务归一（§7.5-2） ----------------

@pytest.mark.parametrize("recur", ["daily", "weekly", "monthly", "yearly"])
def test_recurring_normalized_not_overdue(db, recur):
    tid = _add("重复", due=TODAY - timedelta(days=1), recur=recur)
    v = _view_of(vm.build_views(), tid)
    assert v.eff_date >= TODAY          # 归一后不早于今天
    assert not v.is_overdue
    assert v.group in (vm.GROUP_TODAY, vm.GROUP_WEEK, vm.GROUP_LATER)


def test_recurring_daily_eff_is_today(db):
    tid = _add("每天", due=TODAY - timedelta(days=5), recur="daily")
    v = _view_of(vm.build_views(), tid)
    assert v.eff_date == TODAY
    assert v.group == vm.GROUP_TODAY


# ---------------- long 不进紧急组（§7.5-3） ----------------

def test_long_never_in_urgent_groups(db):
    t_over = _add("逾期长期", due=TODAY - timedelta(days=3), task_type="long")
    t_today = _add("今天长期", due=TODAY, task_type="long")
    views = vm.build_views()
    v_over, v_today = _view_of(views, t_over), _view_of(views, t_today)
    assert v_over.group == vm.GROUP_GOAL
    assert v_today.group == vm.GROUP_GOAL
    assert v_over.badge == ("已逾期", "urgent")   # 借 urgent 色
    assert v_today.badge == ("今天", "today")


def test_long_group_internal_order(db):
    _add_display_kind_column()
    t_nodate = _add("无日期目标", due=None, task_type="long")
    t_future = _add("未来目标", due=TODAY + timedelta(days=9), task_type="long")
    t_today = _add("今天目标", due=TODAY, task_type="long")
    t_over = _add("逾期目标", due=TODAY - timedelta(days=2), task_type="long")
    groups = dict(vm.group_views(vm.build_views()))
    ids = [v.id for v in groups[vm.GROUP_GOAL]]
    assert ids == [t_over, t_today, t_future, t_nodate]  # 逾期→今天→未来→无日期


def test_habit_group(db):
    _add_display_kind_column()
    t = _add("每天喝水", due=None, task_type="long")
    _set_display_kind(t, "habit")
    v = _view_of(vm.build_views(), t)
    assert v.group == vm.GROUP_HABIT
    assert v.display_kind == "habit"


# ---------------- streak（§7.5-4） ----------------

def test_streak_five_days(db):
    t = _add("习惯", task_type="long")
    for i in range(1, 6):  # 昨天起连续 5 天
        _journal_on(t, TODAY - timedelta(days=i))
    _journal_on(t, TODAY)  # 今天也打卡 → 6
    v = _view_of(vm.build_views(), t)
    assert v.streak == 6
    assert v.checked_today is True


def test_streak_today_missing_not_broken(db):
    t = _add("习惯", task_type="long")
    for i in range(1, 4):  # 昨天起连续 3 天，今天未打卡
        _journal_on(t, TODAY - timedelta(days=i))
    v = _view_of(vm.build_views(), t)
    assert v.streak == 3      # 今天没打不断链
    assert v.checked_today is False


def test_streak_broken_yesterday(db):
    t = _add("习惯", task_type="long")
    _journal_on(t, TODAY - timedelta(days=2))  # 昨天断了
    v = _view_of(vm.build_views(), t)
    assert v.streak == 0


def test_streak_same_day_two_journals_count_once(db):
    t = _add("习惯", task_type="long")
    _journal_on(t, TODAY, n=2)  # 同日两条
    _journal_on(t, TODAY - timedelta(days=1))
    v = _view_of(vm.build_views(), t)
    assert v.streak == 2
    assert v.journal_count == 3


def test_checkins_20d(db):
    t = _add("习惯", task_type="long")
    _journal_on(t, TODAY)                      # 今天
    _journal_on(t, TODAY - timedelta(days=19))  # 20 天窗口最早一天
    _journal_on(t, TODAY - timedelta(days=20))  # 窗口外
    v = _view_of(vm.build_views(), t)
    assert len(v.checkins_20d) == 20
    assert v.checkins_20d[-1] is True   # 今天在末位
    assert v.checkins_20d[0] is True    # 19 天前在首位
    assert v.checkins_20d[1] is False   # 18 天前空


# ---------------- display_kind 缺失（§7.5-5，模拟老库） ----------------

def test_long_defaults_goal_when_column_missing(db):
    # 本 fixture 建的是 P1 旧库，没有 display_kind 列
    t = _add("老长期", due=TODAY + timedelta(days=30), task_type="long")
    v = _view_of(vm.build_views(), t)
    assert v.display_kind == "goal"
    assert v.group == vm.GROUP_GOAL


# ---------------- cd_text 边界（§7.5-6） ----------------

def test_cd_text_boundaries(db):
    t0 = _add("今天", due=TODAY)
    t1 = _add("昨天", due=TODAY - timedelta(days=1))
    t2 = _add("无日期", due=None)
    t3 = _add("五天后", due=TODAY + timedelta(days=5))
    views = vm.build_views()
    assert (_view_of(views, t0).cd_text, _view_of(views, t0).cd_unit) == ("今天", "")
    assert _view_of(views, t1).cd_text == "已逾期 1 天"
    assert (_view_of(views, t2).cd_text, _view_of(views, t2).cd_unit) == ("未设目标日", "")
    assert (_view_of(views, t3).cd_text, _view_of(views, t3).cd_unit) == ("5", "天")


# ---------------- 其余派生 ----------------

def test_badge_near_only_short(db):
    t = _add("三天后", due=TODAY + timedelta(days=3))
    v = _view_of(vm.build_views(), t)
    assert v.badge == ("3天后", "near")


def test_progress_goal(db):
    created = (TODAY - timedelta(days=5)).isoformat() + " 00:00:00"
    t = _add("目标", due=TODAY + timedelta(days=5), task_type="long",
             created=created)
    v = _view_of(vm.build_views(), t)
    assert v.progress == pytest.approx(0.5)


def test_progress_none_without_due(db):
    t = _add("目标", due=None, task_type="long")
    assert _view_of(vm.build_views(), t).progress is None


def test_filter_counts(db):
    _add("昨天", due=TODAY - timedelta(days=1))
    _add("今天", due=TODAY)
    _add("三天后", due=TODAY + timedelta(days=3))
    _add("十天后", due=TODAY + timedelta(days=10))
    _add("无日期", due=None)
    _add("长期", due=TODAY + timedelta(days=30), task_type="long")
    _add("已完成", due=TODAY, status="done")
    c = vm.filter_counts(vm.build_views())
    assert c == {vm.FILTER_ALL: 6, vm.FILTER_LONG: 1, vm.FILTER_WEEK: 3,
                 vm.FILTER_TODAY: 1, vm.FILTER_DONE: 1}


def test_day_view(db):
    t1 = _add("上午", due=TODAY, due_time="09:00")
    t2 = _add("下午", due=TODAY, due_time="15:00")
    t3 = _add("已完成", due=TODAY, status="done", due_time="08:00")
    t4 = _add("每周", due=TODAY - timedelta(days=7), recur="weekly")
    _add("明天", due=TODAY + timedelta(days=1))   # 不该出现
    _add("无日期", due=None)                      # 不该出现
    views = vm.build_views_for_day(TODAY)
    ids = [v.id for v in views]
    assert ids[:2] == [t1, t2]          # 未完成按时间升序
    assert t4 in ids                    # 重复命中当天
    assert ids[-1] == t3                # 已完成排最后
    assert _view_of(views, t4).eff_date == TODAY
