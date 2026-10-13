# -*- coding: utf-8 -*-
"""倒计时规格单测（设计文档 §3.1 全部用例）。"""
import unittest
from datetime import date, datetime

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
        self.assertEqual(add_months(date(2026, 1, 31), 1).isoformat(), "2026-02-28")
        self.assertEqual(add_months(date(2024, 1, 31), 1).isoformat(), "2024-02-29")
        self.assertEqual(add_months(date(2026, 10, 9), 4).isoformat(), "2027-02-09")
        self.assertEqual(add_months(date(2026, 12, 15), 2).isoformat(), "2027-02-15")


if __name__ == "__main__":
    unittest.main()
