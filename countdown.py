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
