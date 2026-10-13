# -*- coding: utf-8 -*-
"""P2 匹配合一 + P3 终检单测。"""
import unittest
from datetime import date

from parse_pipeline import dedup_chunks, match, overlap_score, title_sim
from text_parser import Candidate, ParsedEvent, parse_pipeline


TODAY = date(2026, 10, 9)


def _rule(sids, title, due=None, detail=""):
    ev = ParsedEvent(title=title, due_date=due, raw=title, detail=detail)
    ev.source_sentences = sids
    return ev


class TestSimilarity(unittest.TestCase):
    def test_overlap_rule_subset_of_ai(self):
        # 规则抓 1 句、AI 合并 3 句 → Jaccard=1/3 但重叠系数=1.0
        self.assertEqual(overlap_score(["S2"], ["S1", "S2", "S3"]), 1.0)

    def test_overlap_disjoint(self):
        self.assertEqual(overlap_score(["S1"], ["S2"]), 0.0)

    def test_title_sim_identical(self):
        self.assertGreater(title_sim("SAIS大赛报名", "SAIS大赛报名"), 0.99)

    def test_title_sim_unrelated(self):
        self.assertLess(title_sim("组会讨论", "交水电费"), 0.3)


class TestMatch(unittest.TestCase):
    def test_paired_with_merged_sentences(self):
        rules = [_rule(["S2"], "组会讨论实验进度", detail="讨论实验安排")]
        ai = [{"sentences": ["S1", "S2", "S3"], "title": "组会讨论",
               "date_id": None, "time_id": None, "recur_id": None,
               "detail": "讨论实验安排"}]
        out, stats = match(rules, ai, [], [], [],
                           {"S1", "S2", "S3"}, None, TODAY)
        self.assertEqual(stats["paired"], 1)
        self.assertEqual(len(out), 1)
        self.assertFalse(getattr(out[0], "ai_new", False))
        self.assertEqual(out[0].title, "组会讨论")
        # 来源句子 = 双方并集
        self.assertEqual(set(out[0].source_sentences), {"S1", "S2", "S3"})

    def test_sais_date_via_candidate(self):
        res = parse_pipeline("SAIS大赛报名截止时间为次年3月1日。", TODAY)
        d1 = next(c for c in res.date_cands if c.value == "2027-03-01")
        ai = [{"sentences": ["S1"], "title": "SAIS大赛报名",
               "date_id": d1.cid, "time_id": None, "recur_id": None,
               "detail": "次年3月1日截止"}]
        out, stats = match(res.events, ai, res.date_cands, res.time_cands,
                           res.recur_cands, {s.sid for s in res.sentences},
                           None, TODAY)
        self.assertEqual(stats["paired"], 1)
        self.assertEqual(out[0].due_date, "2027-03-01")

    def test_ai_new_two_tiers(self):
        rules = [_rule(["S1"], "规则事项甲")]
        date_cands = [Candidate("D1", "次年3月1日", "2027-03-01")]
        ai = [
            # AI 独有：未来 date_id → 自动勾选
            {"sentences": ["S2"], "title": "乙事项完全不同",
             "date_id": "D1", "time_id": None, "recur_id": None, "detail": ""},
            # AI 独有：无 date_id → 需人工确认
            {"sentences": ["S3"], "title": "丙事项也不像",
             "date_id": None, "time_id": None, "recur_id": None, "detail": ""},
        ]
        out, stats = match(rules, ai, date_cands, [], [],
                           {"S1", "S2", "S3"}, None, TODAY)
        self.assertEqual(stats["paired"], 0)
        self.assertEqual(stats["ai_new"], 2)
        auto = {ev.title: ev.auto_checked for ev in out
                if getattr(ev, "ai_new", False)}
        self.assertEqual(auto["乙事项完全不同"], True)
        self.assertEqual(auto["丙事项也不像"], False)

    def test_user_edited_title_not_overwritten(self):
        rules = [_rule(["S1"], "规则原标题")]
        ai = [{"sentences": ["S1"], "title": "AI 新标题",
               "date_id": None, "time_id": None, "recur_id": None,
               "detail": "AI 补充备注"}]
        out, stats = match(rules, ai, [], [], [], {"S1"},
                           {0: {"title"}}, TODAY)
        self.assertEqual(stats["paired"], 1)
        self.assertEqual(out[0].title, "规则原标题")
        self.assertEqual(out[0].detail, "AI 补充备注")

    def test_invalid_sid_and_out_of_range_id_dropped(self):
        rules = [_rule(["S1"], "规则事项")]
        ai = [{"sentences": ["S99"], "title": "幽灵事项",
               "date_id": None, "time_id": None, "recur_id": None, "detail": ""},
              {"sentences": ["S1"], "title": "正常事项",
               "date_id": "D99", "time_id": "T9", "recur_id": "R7",
               "detail": ""}]
        out, stats = match(rules, ai, [], [], [], {"S1"}, None, TODAY)
        self.assertEqual(stats["ai_kept"], 1)      # S99 整条丢弃
        self.assertEqual(stats["paired"], 1)       # 正常事项仍配对
        self.assertEqual(out[0].due_date, None)    # 越界 ID → null
        self.assertEqual(out[0].title, "正常事项")


class TestAiJunkGate(unittest.TestCase):
    def test_property_and_fragment_rows_dropped(self):
        rules = [_rule(["S1"], "甲赛事")]
        ai = [
            {"sentences": ["S2"], "title": "方向：机器学习、计算数学",
             "date_id": None, "time_id": None, "recur_id": None, "detail": ""},
            {"sentences": ["S3"], "title": "术证明。",
             "date_id": None, "time_id": None, "recur_id": None, "detail": ""},
            {"sentences": ["S4"], "title": "真正的新事项",
             "date_id": None, "time_id": None, "recur_id": None, "detail": ""},
        ]
        out, stats = match(rules, ai, [], [], [],
                           {"S1", "S2", "S3", "S4"}, None, TODAY)
        self.assertEqual(stats["ai_junk"], 2)
        self.assertEqual(stats["ai_new"], 1)
        self.assertEqual(
            [ev.title for ev in out if getattr(ev, "ai_new", False)],
            ["真正的新事项"])

    def test_junk_ai_title_does_not_overwrite_rule_title(self):
        rules = [_rule(["S1"], "ICSAI 2026 学术会议")]
        ai = [{"sentences": ["S1"], "title": "术证明。",
               "date_id": None, "time_id": None, "recur_id": None,
               "detail": "备注仍可合并"}]
        out, stats = match(rules, ai, [], [], [], {"S1"}, None, TODAY)
        self.assertEqual(stats["paired"], 1)
        self.assertEqual(out[0].title, "ICSAI 2026 学术会议")
        self.assertEqual(out[0].detail, "备注仍可合并")


class TestDedup(unittest.TestCase):
    def test_overlap_sentence_duplicate_merged(self):
        # 块间重叠 1 句（S2），标题几乎一致 → 合并，保留 detail 更长的
        rows = [
            {"sentences": ["S1", "S2"], "title": "SAIS大赛报名",
             "date_id": "D1", "detail": "短"},
            {"sentences": ["S2", "S3"], "title": "SAIS 大赛报名",
             "date_id": "D1", "detail": "更长的备注信息"},
        ]
        out = dedup_chunks(rows)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["detail"], "更长的备注信息")

    def test_unrelated_kept(self):
        rows = [
            {"sentences": ["S1"], "title": "甲事项",
             "date_id": None, "detail": ""},
            {"sentences": ["S2"], "title": "乙事项",
             "date_id": None, "detail": ""},
        ]
        self.assertEqual(len(dedup_chunks(rows)), 2)


class TestFinalCheck(unittest.TestCase):
    def test_past_date_flagged(self):
        date_cands = [Candidate("D1", "旧日期", "2025-01-01")]
        ai = [{"sentences": ["S1"], "title": "过去事项",
               "date_id": "D1", "time_id": None, "recur_id": None,
               "detail": ""}]
        out, stats = match([], ai, date_cands, [], [], {"S1"}, None, TODAY)
        ev = out[0]
        self.assertEqual(ev.auto_checked, False)   # 过去日期不自动勾选
        self.assertTrue(any("日期已过去" in w for w in ev.warnings))

    def test_long_title_truncated_and_empty_dropped(self):
        ai = [{"sentences": ["S1"], "title": "长" * 150,
               "date_id": None, "time_id": None, "recur_id": None, "detail": ""},
              {"sentences": ["S1"], "title": "   ",
               "date_id": None, "time_id": None, "recur_id": None, "detail": ""}]
        out, stats = match([], ai, [], [], [], {"S1"}, None, TODAY)
        self.assertEqual(stats["ai_kept"], 1)      # 空标题丢弃
        self.assertEqual(len(out[0].title), 100)   # 长度硬切
        self.assertEqual(out[0].detail, "")


if __name__ == "__main__":
    unittest.main()
