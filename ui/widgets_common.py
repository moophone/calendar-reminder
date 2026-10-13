# -*- coding: utf-8 -*-
"""通用小控件、倒计时徽标与程序化图标（纯搬运自 ui.py）。"""
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


class NoWheelDateEdit(QDateEdit):
    """滚轮不改日期：鼠标从日期框上滑过时不会误把年月滚走。"""
    def wheelEvent(self, event):
        event.ignore()


class NoWheelTimeEdit(QTimeEdit):
    def wheelEvent(self, event):
        event.ignore()


def _eff_datetime(task: dict) -> datetime | None:
    """任务的下一次到期时刻（含时间）；无目标日返回 None。"""
    if not task.get("due_date"):
        return None
    d = datetime.strptime(task["due_date"], "%Y-%m-%d").date()
    if task["recur"] != RECUR_NONE:
        d = next_occurrence(d, task["recur"], date.today())
    t = datetime.combine(d, datetime.min.time())
    if task.get("due_time"):
        h, m = map(int, task["due_time"].split(":"))
        t = t.replace(hour=h, minute=m)
    return t


def countdown_str_for(task: dict, now: datetime | None = None) -> str:
    """任务 → 复合倒计时文案；无目标日 → 未设目标日；逾期 → 已逾期 X 天。"""
    if not task.get("due_date"):
        return "未设目标日"
    now = now or datetime.now()
    target = _eff_datetime(task)
    if target is None or target < now:
        end = target.date() if target else now.date()
        return f"已逾期 {(now.date() - end).days} 天" if target else "未设目标日"
    return countdown(now, target)


def countdown_badge(task: dict, now: datetime | None = None):
    """行内倒计时徽标 → (文案, 底色, 字色)。紧急优先级高于类型配色（§5）。"""
    now = now or datetime.now()
    text = countdown_str_for(task, now)
    if not task.get("due_date"):
        return text, C.HOVER_BG, C.TEXT_SUB
    target = _eff_datetime(task)
    if target < now:
        return text, C.URGENT_BG, C.URGENT_FG
    if (target - now).total_seconds() < 86400:
        return text, C.URGENT_BG, C.URGENT_FG      # 24h 内：红（覆盖类型色）
    if task.get("task_type") == "long":
        if (target.date() - now.date()).days <= 7:
            return text, C.NEAR_BG, C.NEAR_FG      # 长期进入 7 天：橙
        return text, C.LONG_BG, C.LONG_FG          # 长期：深金
    return text, C.NEAR_BG, C.NEAR_FG              # 短期（面板内本就 ≤7 天）


def make_badge(text: str, bg: str, fg: str) -> QLabel:
    lab = QLabel(text)
    lab.setStyleSheet(
        f"background:{bg};color:{fg};border-radius:8px;"
        "padding:1px 8px;font-size:11px;font-weight:700;")
    lab.setFixedHeight(18)
    return lab


# ============================== 图标绘制 ==============================

def make_app_icon(px: int = 64) -> QIcon:
    """用 QPainter 画一个圆角日历图标，不依赖任何外部图片。"""
    pic = QPixmap(px, px)
    pic.fill(Qt.GlobalColor.transparent)
    p = QPainter(pic)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    grad = QLinearGradient(0, 0, px, px)
    grad.setColorAt(0, QColor(C.ACCENT).lighter(120))
    grad.setColorAt(1, QColor(C.ACCENT))
    path = QPainterPath()
    path.addRoundedRect(QRectF(px * 0.08, px * 0.12, px * 0.84, px * 0.78),
                        px * 0.18, px * 0.18)
    p.fillPath(path, QBrush(grad))

    # 顶部两个挂耳
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("white"))
    p.drawRoundedRect(QRectF(px * 0.30, px * 0.05, px * 0.07, px * 0.16),
                      px * 0.03, px * 0.03)
    p.drawRoundedRect(QRectF(px * 0.63, px * 0.05, px * 0.07, px * 0.16),
                      px * 0.03, px * 0.03)

    # 白色顶条
    p.drawRoundedRect(QRectF(px * 0.08, px * 0.12, px * 0.84, px * 0.16),
                      px * 0.16, px * 0.16)
    # 日期格点
    for row, col in [(0, 0), (0, 1), (0, 2), (1, 0), (1, 1), (1, 2)]:
        cx = px * (0.30 + col * 0.20)
        cy = px * (0.48 + row * 0.15)
        p.drawEllipse(QPointF(cx, cy), px * 0.035, px * 0.035)
    # 高亮一颗
    p.setBrush(QColor(C.WARN).lighter(130))
    p.drawEllipse(QPointF(px * 0.70, px * 0.63), px * 0.045, px * 0.045)
    p.end()
    return QIcon(pic)
