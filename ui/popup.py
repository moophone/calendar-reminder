# -*- coding: utf-8 -*-
"""到点提醒弹窗（纯搬运自 ui.py）。"""
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
from .widgets_common import make_app_icon

def _badge_qss():
    """kind → (底色, 文案)，运行时取 C（换肤后立即生效）。"""
    return {
        KIND_TODAY: (C.ACCENT, "今天"),
        KIND_OVERDUE: (C.URGENT_FG, "逾期"),
        KIND_LEAD: (C.NEAR_FG, "预警"),
        KIND_RECUR_TODAY: (C.RECUR_DOT, "重复"),
        KIND_LONG: (C.LONG_FG, "长期"),
    }


class ReminderPopup(QWidget):
    """非模态、置顶的汇总提醒；点"稍后"撤销去重记录，10 分钟后再次弹。"""

    dismissed = pyqtSignal()
    snoozed = pyqtSignal(list)      # list[ReminderItem]
    task_done = pyqtSignal(int)

    def __init__(self, items: list, parent=None):
        super().__init__(parent)
        self.items = items
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"QWidget{{background:{C.CARD};}} QLabel{{background:transparent;}}")
        self.setFixedWidth(400)
        self.drag_pos = None

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(8)

        head = QHBoxLayout()
        icon_lab = QLabel()
        icon_lab.setPixmap(make_app_icon(34).pixmap(34, 34))
        t = QLabel(f"你有 {len(items)} 件事别忘了")
        t.setStyleSheet("font-size:16px;font-weight:800;")
        x_btn = QPushButton("×")
        x_btn.setFixedSize(40, 40)   # §8.4 ×热区 ≥40px
        x_btn.setToolTip("关闭 (Esc)")
        x_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        x_btn.setStyleSheet(
            f"QPushButton{{border:none;background:transparent;"
            f"font-size:18px;color:{C.TEXT_SUB};border-radius:8px;}}"
            f"QPushButton:hover{{background:{C.HOVER_BG};color:{C.DANGER};}}")
        x_btn.clicked.connect(self.close)
        head.addWidget(icon_lab)
        head.addWidget(t, 1)
        head.addWidget(x_btn)
        root.addLayout(head)
        root.addWidget(self._line())

        for it in items:
            root.addWidget(self._row(it))

        root.addWidget(self._line())
        btns = QHBoxLayout()
        snooze = QPushButton("稍后全部（10 分钟）")
        snooze.setCursor(Qt.CursorShape.PointingHandCursor)
        snooze.clicked.connect(self._do_snooze)
        ok = QPushButton("关闭")
        ok.setObjectName("primary")
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.clicked.connect(self.close)
        btns.addStretch(1)
        btns.addWidget(snooze)
        btns.addWidget(ok)
        root.addLayout(btns)

        self.adjustSize()
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        g = screen.availableGeometry()
        self.move(g.right() - self.width() - 24,
                  g.bottom() - self.height() - 24)

    def _line(self):
        ln = QFrame()
        ln.setFixedHeight(1)
        ln.setStyleSheet(f"background:{C.BORDER};")
        return ln

    def _row(self, it):
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 4, 0, 4)
        color, text = _badge_qss()[it.kind]
        badge = QLabel(text)
        badge.setStyleSheet(
            f"background:{color};color:white;border-radius:8px;"
            "padding:2px 8px;font-size:11px;font-weight:700;"
        )
        badge.setFixedHeight(20)
        mid = QVBoxLayout()
        mid.setSpacing(1)
        tt = QLabel(it.task["title"])
        tt.setStyleSheet("font-size:13px;font-weight:600;")
        tt.setWordWrap(True)
        ex = QLabel(human_explain(it.task))
        ex.setStyleSheet(f"color:{C.TEXT_SUB};font-size:12px;")
        mid.addWidget(tt)
        mid.addWidget(ex)
        done = QPushButton("完成")
        done.setFixedWidth(52)
        done.setCursor(Qt.CursorShape.PointingHandCursor)
        done.setStyleSheet(
            f"QPushButton{{border:1px solid {C.BORDER};border-radius:8px;"
            "padding:4px 0;font-weight:600;}"
            f"QPushButton:hover{{border-color:{C.RECUR_DOT};color:{C.RECUR_DOT};}}"
        )
        done.clicked.connect(lambda: self._do_done(it.task["id"]))
        later = QPushButton("稍后")
        later.setFixedWidth(52)
        later.setCursor(Qt.CursorShape.PointingHandCursor)
        later.setToolTip("本条 10 分钟后再提醒")
        later.setStyleSheet(
            f"QPushButton{{border:1px solid {C.BORDER};border-radius:8px;"
            "padding:4px 0;font-weight:600;}"
            f"QPushButton:hover{{border-color:{C.WARN};color:{C.WARN};}}"
        )
        later.clicked.connect(lambda: self._do_snooze_one(it, w))
        side = QVBoxLayout()
        side.setSpacing(4)
        side.addWidget(done)
        side.addWidget(later)
        lay.addWidget(badge)
        lay.addLayout(mid, 1)
        lay.addLayout(side)
        return w

    def _do_snooze_one(self, it, row_widget):
        """单条稍后：只 snooze 这一项，其余留在窗内（§8.4）。"""
        self.snoozed.emit([it])
        self.items = [x for x in self.items if x.task["id"] != it.task["id"]]
        row_widget.hide()
        row_widget.deleteLater()
        if not self.items:
            self.close()

    def _do_snooze(self):
        self.snoozed.emit(list(self.items))
        self.close()

    def _do_done(self, task_id):
        self.task_done.emit(task_id)
        self.items = [it for it in self.items if it.task["id"] != task_id]
        if not self.items:
            self.close()
        else:
            # 简化：完成后关闭，由引擎下轮刷新
            self.close()

    def closeEvent(self, e):
        self.dismissed.emit()
        super().closeEvent(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(e)

    # 无边框拖动
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.drag_pos = e.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, e):
        if self.drag_pos:
            self.move(e.globalPosition().toPoint() - self.drag_pos)

    def mouseReleaseEvent(self, e):
        self.drag_pos = None
