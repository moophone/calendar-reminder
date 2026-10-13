# -*- coding: utf-8 -*-
"""详情面板 _clear 回归：连续切换详情不得叠加旧标题/×按钮（子布局下钻清理）。"""
import os
import unittest
from datetime import date

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QTimer, QEventLoop
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton

import viewmodel as vm
from ui.detail_panel import DetailPanel


def _flush(app):
    """提升事件循环层级，冲掉 deleteLater 队列。"""
    loop = QEventLoop()
    QTimer.singleShot(0, loop.quit)
    loop.exec()
    app.processEvents()


def _view(tid: int, title: str) -> vm.TaskView:
    return vm.TaskView(
        id=tid, title=title, detail="", task_type="short", display_kind=None,
        status="pending", eff_date=date(2026, 10, 20), due_time=None,
        recur="none", days=9, is_overdue=False, is_today=False,
        group=vm.GROUP_LATER, badge=None, explain="还有 9 天到期",
        cd_text="9", cd_unit="天")


class TestDetailClear(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _count_text(self, panel, text):
        return sum(1 for l in panel.findChildren(QLabel) if l.text() == text)

    def _count_x(self, panel):
        return sum(1 for b in panel.findChildren(QPushButton) if b.text() == "×")

    def test_switch_no_ghost(self):
        p = DetailPanel()
        titles = ["任务甲甲甲", "任务乙乙乙", "任务丙丙丙"]
        for i, t in enumerate(titles):
            p.show_view(_view(i + 1, t), [])
            _flush(self.app)
        self.assertEqual(sum(self._count_text(p, t) for t in titles[:-1]), 0)
        self.assertEqual(self._count_text(p, titles[-1]), 1)
        self.assertEqual(self._count_x(p), 1)
        p.deleteLater()

    def test_view_empty_view_roundtrip(self):
        p = DetailPanel()
        p.show_view(_view(1, "第一轮"), [])
        _flush(self.app)
        p.show_empty([])
        _flush(self.app)
        p.show_view(_view(2, "第二轮"), [])
        _flush(self.app)
        self.assertEqual(self._count_text(p, "第一轮"), 0)
        self.assertEqual(self._count_text(p, "第二轮"), 1)
        self.assertEqual(self._count_x(p), 1)
        p.deleteLater()


if __name__ == "__main__":
    unittest.main()
