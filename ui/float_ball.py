# -*- coding: utf-8 -*-
"""桌面悬浮球（纯搬运自 ui.py）。"""
from datetime import date, datetime

from PyQt6.QtCore import (
    Qt, QDate, QSize, QTimer, QPoint, QPointF, QRectF, QThread, pyqtSignal,
    QVariantAnimation,
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


class FloatBall(QWidget):
    clicked_ball = pyqtSignal()
    quit_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    hide_requested = pyqtSignal()
    check_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(64, 64)
        self._drag = None
        self._moved = False
        self._today = 0
        self._overdue = 0
        # 逾期脉冲：外圈扩散环 2s 循环（§8.5，仅逾期 >0 时启动）
        self._pulse = 0.0
        self._pulse_anim = QVariantAnimation(self)
        self._pulse_anim.setStartValue(0.0)
        self._pulse_anim.setEndValue(1.0)
        self._pulse_anim.setDuration(2000)
        self._pulse_anim.setLoopCount(-1)
        self._pulse_anim.valueChanged.connect(self._on_pulse)
        self._restore_pos()

    def _on_pulse(self, v):
        self._pulse = float(v)
        self.update()

    def set_counts(self, today: int, overdue: int):
        if (self._today, self._overdue) != (today, overdue):
            self._today, self._overdue = today, overdue
            self.update()
            tip = f"今日待办 {today} 件"
            if overdue:
                tip += f"，逾期 {overdue} 件"
            self.setToolTip(tip if (today or overdue) else "今天没有待办，干得漂亮")
        # 脉冲随逾期状态启停
        if overdue > 0 and self._pulse_anim.state() != QVariantAnimation.State.Running:
            self._pulse_anim.start()
        elif overdue == 0 and self._pulse_anim.state() == QVariantAnimation.State.Running:
            self._pulse_anim.stop()
            self._pulse = 0.0
            self.update()

    def _restore_pos(self):
        raw = storage.get_setting("ball_pos")   # §6.5 单键 "x,y"
        pos = None
        if raw:
            try:
                x, y = (int(v) for v in raw.split(","))
                pos = QPoint(x, y)
            except (ValueError, TypeError):
                pos = None
        if pos is None:
            # 兼容 v1.0 遗留 ball_x/ball_y 两键
            x, y = storage.get_setting("ball_x"), storage.get_setting("ball_y")
            if x is not None and y is not None:
                pos = QPoint(int(x), int(y))
        if pos is None:
            screen = QApplication.primaryScreen().availableGeometry()
            pos = QPoint(screen.right() - 90, screen.top() + 180)
        # 边界钳制在当前屏幕内（§8.5）
        g = QApplication.primaryScreen().availableGeometry()
        pos.setX(max(g.left(), min(pos.x(), g.right() - self.width())))
        pos.setY(max(g.top(), min(pos.y(), g.bottom() - self.height())))
        self.move(pos)

    def _save_pos(self):
        storage.set_setting("ball_pos", f"{self.pos().x()},{self.pos().y()}")

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()

        # 逾期脉冲扩散环（最底层）
        if self._overdue and self._pulse > 0:
            ring_r = (w - 12) / 2 + self._pulse * 6
            alpha = int(120 * (1 - self._pulse))
            ring_c = QColor(C.DANGER)
            ring_c.setAlpha(alpha)
            p.setPen(QPen(ring_c, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(w / 2, w / 2 - 1), ring_r, ring_r)

        # 阴影（§9.6 各主题 shadow-color）
        p.setPen(Qt.PenStyle.NoPen)
        sr, sg, sb = C.SHADOW_RGB
        p.setBrush(QColor(sr, sg, sb, 26))
        p.drawEllipse(QRectF(5, 7, w - 10, w - 10))

        grad = QLinearGradient(0, 0, w, w)
        if self._overdue:
            grad.setColorAt(0, QColor(C.DANGER).lighter(115))
            grad.setColorAt(1, QColor(C.DANGER))
        elif self._today:
            grad.setColorAt(0, QColor(C.ACCENT).lighter(120))
            grad.setColorAt(1, QColor(C.ACCENT))
        else:
            grad.setColorAt(0, QColor(C.RECUR_DOT).lighter(125))
            grad.setColorAt(1, QColor(C.RECUR_DOT))
        p.setBrush(QBrush(grad))
        p.drawEllipse(QRectF(4, 4, w - 12, w - 12))

        # 中心数字/勾
        p.setPen(QPen(QColor("white")))
        f = QFont("Microsoft YaHei UI", 18, QFont.Weight.Black)
        p.setFont(f)
        n = self._today + self._overdue
        text = str(n) if n else "✓"
        if n >= 100:
            f.setPointSize(11)
            p.setFont(f)
        rect = QRectF(4, 4, w - 12, w - 12)
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

        # 逾期小红点
        if self._overdue:
            p.setBrush(QColor(C.WARN).lighter(135))
            p.setPen(QPen(QColor("white"), 2))
            p.drawEllipse(QPointF(w - 16, 16), 7, 7)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.pos()
            self._moved = False

    def mouseMoveEvent(self, e):
        if self._drag:
            new_pos = e.globalPosition().toPoint() - self._drag
            if (new_pos - self.pos()).manhattanLength() > 4:
                self._moved = True
            self.move(new_pos)

    def mouseReleaseEvent(self, e):
        if self._drag is not None and not self._moved:
            self.clicked_ball.emit()
        if self._moved:
            self._save_pos()
        self._drag = None

    def mouseDoubleClickEvent(self, e):
        # 双击 = 立即检查（§8.5 简化案：单击显隐照常，双击额外触发幂等检查）
        if e.button() == Qt.MouseButton.LeftButton:
            self.check_requested.emit()

    def contextMenuEvent(self, e):
        m = QMenu(self)
        a_open = QAction("打开主界面", self)
        a_open.triggered.connect(self.clicked_ball.emit)
        a_check = QAction("立即检查提醒", self)
        a_check.triggered.connect(self.check_requested.emit)
        a_auto = QAction("开机自启", self, checkable=True)
        import autostart
        a_auto.setChecked(autostart.is_autostart_enabled())
        a_auto.toggled.connect(lambda on: autostart.set_autostart(on))
        a_hide = QAction("隐藏小球（托盘可找回）", self)
        a_hide.triggered.connect(self.hide_requested.emit)
        a_quit = QAction("退出", self)
        a_quit.triggered.connect(self.quit_requested.emit)
        for a in (a_open, a_check, a_auto, a_hide):
            m.addAction(a)
        m.addSeparator()
        m.addAction(a_quit)
        m.exec(e.globalPos())
