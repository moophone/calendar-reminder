# -*- coding: utf-8 -*-
"""新建/编辑任务弹窗（纯搬运自 ui.py）。"""
from datetime import date, datetime

from PyQt6.QtCore import (
    Qt, QDate, QSize, QTimer, QPoint, QPointF, QRectF, QThread, pyqtSignal,
)
from PyQt6.QtGui import (
    QColor, QPainter, QPen, QBrush, QFont, QIcon, QPixmap, QLinearGradient,
    QPainterPath, QAction, QCursor,
)
from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QCalendarWidget, QListWidget, QListWidgetItem, QTextEdit, QLineEdit,
    QDialog, QTableWidget, QTableWidgetItem, QCheckBox, QDateEdit, QTimeEdit,
    QComboBox, QMenu, QSystemTrayIcon, QApplication, QFrame, QSizePolicy,
    QHeaderView, QAbstractItemView, QScrollArea,
)

import storage
import llm_fallback
from countdown import countdown
from storage import (
    RECUR_NONE, RECUR_DAILY, RECUR_WEEKLY, RECUR_MONTHLY, RECUR_YEARLY,
    RECUR_LABEL, STATUS_DONE, STATUS_PENDING, next_occurrence,
)
from text_parser import (
    ParsedEvent, parse_pipeline,
)
from parse_pipeline import dedup_chunks, match
from reminders import (
    human_explain, KIND_LABEL,
    KIND_TODAY, KIND_OVERDUE, KIND_LEAD, KIND_RECUR_TODAY, KIND_LONG,
)
from style import C

RECUR_KEYS = [RECUR_NONE, RECUR_DAILY, RECUR_WEEKLY, RECUR_MONTHLY, RECUR_YEARLY]
from .widgets_common import NoWheelDateEdit, NoWheelTimeEdit


class TaskDialog(QDialog):
    def __init__(self, parent=None, task: dict | None = None,
                 default_date: date | None = None):
        super().__init__(parent)
        self.setWindowTitle("编辑事项" if task else "新建事项")
        self.setFixedWidth(420)
        self.task = task
        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        lay.setContentsMargins(20, 20, 20, 20)

        self.title = QLineEdit()
        self.title.setPlaceholderText("要做什么？例如：提交软著材料")
        lay.addWidget(self.title)

        # 类型（长期/短期，全手动指定）
        row0 = QHBoxLayout()
        row0.addWidget(QLabel("类型"))
        self.task_type = QComboBox()
        self.task_type.addItem("短期", "short")
        self.task_type.addItem("长期", "long")
        self.no_due = QCheckBox("无目标日（仅长期）")
        self.no_due.setEnabled(False)
        row0.addWidget(self.task_type, 1)
        row0.addWidget(self.no_due)
        lay.addLayout(row0)

        # 长期形态二选一（§6.2/§8.2：display_kind，默认目标）
        rowk = QHBoxLayout()
        rowk.addWidget(QLabel("形态"))
        self.display_kind = QComboBox()
        self.display_kind.addItem("目标倒计时（大数字 + 进度）", "goal")
        self.display_kind.addItem("每日习惯（连续打卡）", "habit")
        self.display_kind.setEnabled(False)
        rowk.addWidget(self.display_kind, 1)
        lay.addLayout(rowk)
        # habit 诚实限制说明（§6.2：无目标日的习惯没有每日弹窗通道）
        self.habit_hint = QLabel(
            "提示：不填目标日的习惯不会有每日弹窗提醒；"
            "填一个远期目标日即可每天提醒打卡。")
        self.habit_hint.setStyleSheet(f"color:{C.TEXT_SUB};font-size:12px;")
        self.habit_hint.setWordWrap(True)
        self.habit_hint.setVisible(False)
        lay.addWidget(self.habit_hint)

        row1 = QHBoxLayout()
        self.date_edit = NoWheelDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        d = default_date or date.today()
        self.date_edit.setDate(QDate(d.year, d.month, d.day))
        self.all_day = QCheckBox("全天")
        self.all_day.setChecked(True)
        self.time_edit = NoWheelTimeEdit()
        self.time_edit.setDisplayFormat("HH:mm")
        self.time_edit.setEnabled(False)
        self.all_day.toggled.connect(lambda v: self.time_edit.setEnabled(not v))
        row1.addWidget(QLabel("日期"))
        row1.addWidget(self.date_edit, 1)
        row1.addWidget(self.all_day)
        row1.addWidget(self.time_edit)
        lay.addLayout(row1)

        # 长期可无目标日；短期必须填日期
        self.no_due.toggled.connect(self._on_no_due)
        self.task_type.currentIndexChanged.connect(self._on_type_change)
        self.display_kind.currentIndexChanged.connect(
            lambda _=None: self._sync_habit_hint())

        row2 = QHBoxLayout()
        self.recur = QComboBox()
        for k in RECUR_KEYS:
            self.recur.addItem(RECUR_LABEL[k], k)
        self.lead = QLineEdit("1,3")
        self.lead.setPlaceholderText("1,3")
        self.lead.setFixedWidth(90)
        row2.addWidget(QLabel("重复"))
        row2.addWidget(self.recur, 1)
        row2.addWidget(QLabel("提前预警(天)"))
        row2.addWidget(self.lead)
        lay.addLayout(row2)

        self.detail = QTextEdit()
        self.detail.setPlaceholderText("备注/细节（可选）")
        self.detail.setFixedHeight(70)
        lay.addWidget(self.detail)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("保存")
        ok.setObjectName("primary")
        ok.setDefault(True)   # Enter = 保存（全局交互规范 §6）
        ok.clicked.connect(self.accept)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        lay.addLayout(btns)

        if task:
            self.title.setText(task["title"])
            idx = self.task_type.findData(task.get("task_type") or "short")
            self.task_type.setCurrentIndex(max(0, idx))
            kidx = self.display_kind.findData(task.get("display_kind") or "goal")
            self.display_kind.setCurrentIndex(max(0, kidx))
            if not task["due_date"]:
                # 长期任务无目标日
                self.no_due.setChecked(True)
            else:
                dd = datetime.strptime(task["due_date"], "%Y-%m-%d").date()
                self.date_edit.setDate(QDate(dd.year, dd.month, dd.day))
                if task["due_time"]:
                    self.all_day.setChecked(False)
                    h, m = task["due_time"].split(":")
                    from PyQt6.QtCore import QTime
                    self.time_edit.setTime(QTime(int(h), int(m)))
            self.recur.setCurrentIndex(RECUR_KEYS.index(task["recur"]))
            self.lead.setText(task.get("lead_days") or "1,3")
            self.detail.setPlainText(task.get("detail") or "")

    def _on_type_change(self, idx):
        is_long = self.task_type.currentData() == "long"
        self.no_due.setEnabled(is_long)
        self.display_kind.setEnabled(is_long)
        if not is_long:
            self.no_due.setChecked(False)
        self._sync_habit_hint()

    def _sync_habit_hint(self):
        is_habit = (self.task_type.currentData() == "long"
                    and self.display_kind.currentData() == "habit")
        self.habit_hint.setVisible(is_habit and self.no_due.isChecked())

    def _on_no_due(self, checked):
        self.date_edit.setEnabled(not checked)
        self.all_day.setEnabled(not checked)
        self.time_edit.setEnabled(
            not checked and not self.all_day.isChecked())
        self._sync_habit_hint()

    def get_data(self) -> dict:
        is_long = self.task_type.currentData() == "long"
        kind = self.display_kind.currentData() if is_long else None
        if self.no_due.isChecked():
            # 长期任务无目标日：due_date/due_time 均为 NULL
            return {
                "title": self.title.text().strip() or "未命名事项",
                "due_date": None,
                "due_time": None,
                "recur": self.recur.currentData(),
                "lead_days": self.lead.text().strip() or "1,3",
                "detail": self.detail.toPlainText().strip(),
                "task_type": self.task_type.currentData(),
                "display_kind": kind,
            }
        qd = self.date_edit.date()
        return {
            "title": self.title.text().strip() or "未命名事项",
            "due_date": f"{qd.year():04d}-{qd.month():02d}-{qd.day():02d}",
            "due_time": None if self.all_day.isChecked()
                        else self.time_edit.time().toString("HH:mm"),
            "recur": self.recur.currentData(),
            "lead_days": self.lead.text().strip() or "1,3",
            "detail": self.detail.toPlainText().strip(),
            "task_type": self.task_type.currentData(),
            "display_kind": kind,
        }
