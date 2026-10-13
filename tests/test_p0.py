# -*- coding: utf-8 -*-
"""P0 融合管线单测：句子编号、候选枚举、短句合并、来源句子映射。"""
import unittest
from datetime import date

from text_parser import parse_pipeline, split_sentences


TODAY = date(2026, 10, 9)

TWO_CONTEST_TEXT = """数学建模协会两大赛事通知。
SAIS大赛报名截止时间为次年3月1日，请各队按时提交。
另一赛事校园赛报名截止时间为11月30日。
报名方式：官网
"""


class TestSplitSentences(unittest.TestCase):
    def test_fragment_merged_into_previous(self):
        sents = split_sentences(TWO_CONTEST_TEXT)
        # "报名方式：官网"（7字、无时间线索）应并入上一句，不独立成句
        self.assertNotIn("报名方式：官网", [s.text for s in sents])
        self.assertTrue(any("报名方式：官网" in s.text for s in sents))

    def test_sid_continuous(self):
        sents = split_sentences(TWO_CONTEST_TEXT)
        self.assertEqual([s.sid for s in sents],
                         [f"S{i+1}" for i in range(len(sents))])


class TestParsePipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = parse_pipeline(TWO_CONTEST_TEXT, TODAY)

    def test_events_at_least_two(self):
        self.assertGreaterEqual(len(self.res.events), 2)

    def test_date_cand_2027_03_01(self):
        # "次年3月1日"：today=2026-10-09，3月1日已过 → 明年
        vals = [c.value for c in self.res.date_cands]
        self.assertIn("2027-03-01", vals)

    def test_date_cand_nov30(self):
        vals = [c.value for c in self.res.date_cands]
        self.assertIn("2026-11-30", vals)

    def test_sids_continuous(self):
        self.assertEqual([s.sid for s in self.res.sentences],
                         [f"S{i+1}" for i in range(len(self.res.sentences))])

    def test_every_event_has_source(self):
        for ev in self.res.events:
            self.assertTrue(ev.source_sentences, msg=f"事件无来源句子: {ev.title}")

    def test_source_refs_valid(self):
        valid = {s.sid for s in self.res.sentences}
        for ev in self.res.events:
            for sid in ev.source_sentences:
                self.assertIn(sid, valid)

    def test_ambiguous_day_only(self):
        # "10号"（只有日没有月）：候选枚举不产出日期候选，事件级标存疑暂定今天
        res = parse_pipeline("10号交材料", TODAY)
        self.assertEqual(res.date_cands, [])
        self.assertTrue(res.events[0].ambiguous)


MD_LIST_TEXT = """赛事清单（10月12日更新），已剔除已截止赛事。
## 一、紧急可冲
按截止时间从近到远排序

### 1. 甲赛事（海报投稿）
- **截止时间**：10月16日 24:00
- **官方入口**：www.example.net
- **费用**：投稿免费
- **方向**：机器学习、AI for Science

### 2. 乙竞赛
- **报名截止**：10月23日 12:00
- **费用**：200元

## 二、长线储备
### 1. 丙挑战赛
- **报名截止**：11月10日
- **比赛时间**：11月15-16日

### 丁赛（无编号标题）
- **报名时间**：次年3月开启
"""


class TestParseStructured(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = parse_pipeline(MD_LIST_TEXT, TODAY)

    def test_one_event_per_heading(self):
        # 4 个三级标题 = 4 件事；属性行/前言/大类标题不独立成事件
        self.assertEqual(len(self.res.events), 4)

    def test_titles_are_event_names(self):
        titles = [ev.title for ev in self.res.events]
        self.assertTrue(titles[0].startswith("甲赛事"))
        self.assertNotIn("费用", " ".join(titles))
        self.assertNotIn("按截止", " ".join(titles))

    def test_deadline_dates(self):
        dues = [ev.due_date for ev in self.res.events]
        self.assertEqual(dues[:3],
                         ["2026-10-16", "2026-10-23", "2026-11-10"])

    def test_clock_times_and_24h(self):
        times = [ev.due_time for ev in self.res.events]
        self.assertEqual(times[0], "23:59")   # 24:00 → 当天 23:59
        self.assertEqual(times[1], "12:00")

    def test_block_prefers_deadline_over_match_day(self):
        # 丙赛事：11月10日报名截止，11月15-16日比赛 → 取更早的截止日
        self.assertEqual(self.res.events[2].due_date, "2026-11-10")

    def test_next_year_month_only_ambiguous(self):
        ev = self.res.events[3]
        self.assertEqual(ev.due_date, "2027-03-01")   # 次年3月
        self.assertTrue(ev.ambiguous)

    def test_detail_keeps_whole_section(self):
        self.assertIn("投稿免费", self.res.events[0].detail)

    def test_sources_valid(self):
        valid = {s.sid for s in self.res.sentences}
        for ev in self.res.events:
            self.assertTrue(ev.source_sentences)
            self.assertTrue(set(ev.source_sentences) <= valid)


class TestJunkLineFilter(unittest.TestCase):
    def test_plain_prose_not_structured(self):
        # 普通短文不走结构化；无日期的属性行被滤掉，正文句子保留
        res = parse_pipeline("通知如下\n费用：200元\n10月23日参加乙竞赛", TODAY)
        titles = " ".join(ev.title for ev in res.events)
        self.assertIn("乙竞赛", titles)
        self.assertNotIn("费用", titles)


class TestScanCands(unittest.TestCase):
    def test_time_cand_pm_half(self):
        res = parse_pipeline("周五下午3点半组会", TODAY)
        self.assertIn("15:30", [c.value for c in res.time_cands])

    def test_recur_cand_weekly(self):
        res = parse_pipeline("每周三交周报", TODAY)
        self.assertIn("weekly", [c.value for c in res.recur_cands])


if __name__ == "__main__":
    unittest.main()
