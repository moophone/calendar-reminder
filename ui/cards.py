# -*- coding: utf-8 -*-
"""任务卡片 TaskCardV2（§8.1.4）：短期 / goal / habit / 已完成 四种形态，
纯渲染 viewmodel.TaskView，不做任何日期运算。

点击模型：卡片自身处理 mousePressEvent 发 clicked 信号；所有被动子控件
（文字、竖条、进度条）WA_TransparentForMouseEvents 透传，按钮为真按钮、
点击时先选中卡片再执行动作——任意位置点击零穿透。
"""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QFontMetrics
from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QHBoxLayout, QVBoxLayout, QFrame,
    QSizePolicy,
)

import viewmodel as vm
from style import C

def _badge_colors(key: str):
    """徽标配色运行时取 C（换肤后立即生效）；模块级 dict 会在 import 时冻结。"""
    return {
        "urgent": (C.URGENT_BG, C.URGENT_FG),
        "today": (C.TODAY_BG, C.ACCENT),
        "near": (C.NEAR_BG, C.NEAR_FG),
    }.get(key, (C.NEAR_BG, C.NEAR_FG))


class _ElideLabel(QLabel):
    """单行省略号标签：宽度不足时 ElideRight，tooltip 留全文（§8.12）。"""

    def __init__(self, text=""):
        super().__init__(text)
        self._full = text

    def resizeEvent(self, e):
        super().resizeEvent(e)
        m = QFontMetrics(self.font())
        elided = m.elidedText(self._full, Qt.TextElideMode.ElideRight,
                              max(self.width() - 2, 0))
        if elided != self.text():
            super().setText(elided)
        self.setToolTip(self._full if elided != self._full else "")


def _passive(w: QWidget) -> QWidget:
    w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    return w


def _label(text, size=12, weight=400, color=None, wrap=False,
           elide=False) -> QLabel:
    # 默认参数在 def 时冻结，故用 None 占位、函数体内运行时取 C
    color = color or C.TEXT_SUB
    lab = _ElideLabel(text) if (elide and not wrap) else QLabel(text)
    f = lab.font()
    f.setPixelSize(size)
    f.setWeight(QFont.Weight(weight))
    lab.setFont(f)
    lab.setStyleSheet(f"color:{color};")
    if wrap:
        lab.setWordWrap(True)
    return _passive(lab)


def _pill(text, bg, fg) -> QLabel:
    lab = QLabel(text)
    f = lab.font()
    f.setPixelSize(12)
    f.setWeight(QFont.Weight(500))
    lab.setFont(f)
    lab.setStyleSheet(
        f"background:{bg};color:{fg};border-radius:4px;padding:1px 6px;")
    lab.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Maximum)
    return _passive(lab)


class StreakGrid(QWidget):
    """habit 卡：最近 20 天打卡竖条，20 根 8×24、间距 3、圆角 2（§8.1.4）。"""

    def __init__(self, checkins: list):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(3)
        for hit in checkins:
            bar = QFrame()
            bar.setFixedSize(8, 24)
            bar.setStyleSheet(
                f"background:{C.LONG_FG if hit else C.HOVER_BG};border-radius:2px;")
            lay.addWidget(_passive(bar))
        lay.addStretch(1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)


class ProgressBar(QWidget):
    """goal 卡：6px 高 pill 进度条（surface-2 底、long 色填充）。"""

    def __init__(self, progress: float):
        super().__init__()
        self.setFixedHeight(6)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        pct = max(0, min(100, round(progress * 100)))
        fill = QFrame()
        fill.setFixedHeight(6)
        fill.setStyleSheet(f"background:{C.LONG_FG};border-radius:3px;")
        lay.addWidget(_passive(fill), pct)
        rest = QFrame()
        rest.setFixedHeight(6)
        rest.setStyleSheet(f"background:{C.HOVER_BG};border-radius:3px;")
        lay.addWidget(_passive(rest), 100 - pct)
        if pct == 0:
            fill.hide()
        if pct == 100:
            rest.hide()


class TaskCardV2(QWidget):
    """一张任务卡。view 分组已确定形态：
    done → 完成态；long+goal / long+habit；其余 → 短期卡。"""

    clicked = pyqtSignal(int)
    action_requested = pyqtSignal(str, int)  # complete/checkin/note/reopen

    def __init__(self, view: vm.TaskView, parent=None):
        super().__init__(parent)
        self.view = view
        self.setObjectName("taskCardV2")
        self.setMinimumHeight(62)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Minimum)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._selected = False
        self._hover = False
        self._build()
        self._apply_style()

    # ---------------- 形态 ----------------

    def _build(self):
        v = self.view
        root = QHBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(10)
        if v.status == "done":
            self._build_done(root)
        elif v.task_type == "long" and v.display_kind == "habit":
            self._build_habit(root)
        elif v.task_type == "long":
            self._build_goal(root)
        else:
            self._build_short(root)

    def _title(self, size=14, weight=500, color=None) -> QLabel:
        lab = _label(self.view.title, size, weight, color or C.TEXT,
                     elide=True)
        lab.setSizePolicy(QSizePolicy.Policy.Expanding,
                          QSizePolicy.Policy.Maximum)
        return lab

    def _badge(self) -> QLabel | None:
        if not self.view.badge:
            return None
        text, key = self.view.badge
        bg, fg = _badge_colors(key)
        return _pill(text, bg, fg)

    def _build_short(self, root: QHBoxLayout):
        v = self.view
        cb = QPushButton("↻" if v.recur != "none" else "")
        cb.setFixedSize(20, 20)
        cb.setCursor(Qt.CursorShape.PointingHandCursor)
        cb.setStyleSheet(
            f"QPushButton{{border:2px solid {C.BORDER};border-radius:10px;"
            f"background:{C.CARD};color:{C.TEXT_SUB};font-size:11px;padding:0;}}"
            f"QPushButton:hover{{border-color:{C.ACCENT};}}")
        if v.recur != "none":
            cb.setToolTip("完成本次，自动排到下一周期")
        else:
            cb.setToolTip("完成")
        cb.clicked.connect(lambda: self._act("complete"))
        root.addWidget(cb, 0, Qt.AlignmentFlag.AlignTop)

        col = QVBoxLayout()
        col.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        title_row.addWidget(self._title())
        col.addLayout(title_row)
        col.addWidget(_label(v.explain, 12, 400, elide=True))
        root.addLayout(col, 1)

        if v.due_time:
            root.addWidget(_pill(v.due_time, C.TODAY_BG, C.ACCENT),
                           0, Qt.AlignmentFlag.AlignVCenter)
        b = self._badge()
        if b:
            root.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)

    def _build_goal(self, root: QHBoxLayout):
        v = self.view
        left = QVBoxLayout()
        left.setSpacing(4)
        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        title_row.addWidget(self._title())
        b = self._badge()
        if b:
            title_row.addWidget(b)
        title_row.addStretch(1)
        left.addLayout(title_row)
        target = f"目标日 {v.eff_date.isoformat()}" if v.eff_date else "未设目标日"
        left.addWidget(_label(target, 13, 400))
        if v.progress is not None:
            left.addWidget(ProgressBar(v.progress))
        root.addLayout(left, 1)

        right = QVBoxLayout()
        right.setSpacing(4)
        n = len(v.cd_text)
        size = 48 if n <= 3 else (32 if n <= 6 else 24)
        cd = _label(v.cd_text + (f" {v.cd_unit}" if v.cd_unit else ""),
                    size, 700, C.ACCENT, wrap=(size == 24))
        cd.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        right.addWidget(cd)
        note_btn = QPushButton("记一笔")
        note_btn.setFixedHeight(28)
        note_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        note_btn.clicked.connect(lambda: self._act("note"))
        right.addWidget(note_btn, 0, Qt.AlignmentFlag.AlignRight)
        root.addLayout(right)

    def _build_habit(self, root: QHBoxLayout):
        v = self.view
        col = QVBoxLayout()
        col.setSpacing(4)
        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        title_row.addWidget(self._title())
        b = self._badge()
        if b:
            title_row.addWidget(b)
        title_row.addStretch(1)
        title_row.addWidget(_label(f"连续 {v.streak} 天", 12, 500, C.LONG_FG))
        col.addLayout(title_row)
        col.addWidget(StreakGrid(v.checkins_20d))
        root.addLayout(col, 1)

        btn = QPushButton("今日已打卡 ✓" if v.checked_today else "今日打卡")
        btn.setFixedHeight(28)
        btn.setEnabled(not v.checked_today)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        if not v.checked_today:
            btn.setObjectName("primary")
        btn.clicked.connect(lambda: self._act("checkin"))
        root.addWidget(btn, 0, Qt.AlignmentFlag.AlignBottom)

    def _build_done(self, root: QHBoxLayout):
        v = self.view
        title = _label(v.title, 14, 500, C.DONE_DOT)
        f = title.font()
        f.setStrikeOut(True)
        title.setFont(f)
        root.addWidget(title)
        day = (v.completed_at or "")[:10]
        if day:
            root.addWidget(_label(f"完成于 {day}", 12, 400))
        root.addStretch(1)
        reopen = QPushButton("重新打开")
        reopen.setFixedHeight(28)
        reopen.setCursor(Qt.CursorShape.PointingHandCursor)
        reopen.clicked.connect(lambda: self._act("reopen"))
        root.addWidget(reopen, 0, Qt.AlignmentFlag.AlignVCenter)

    # ---------------- 状态与事件 ----------------

    def _act(self, name: str):
        self.clicked.emit(self.view.id)          # 按钮点击先选中
        self.action_requested.emit(name, self.view.id)

    def set_selected(self, on: bool):
        if self._selected != on:
            self._selected = on
            self._apply_style()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.view.id)
            event.accept()
            return
        super().mousePressEvent(event)

    def enterEvent(self, event):
        self._hover = True
        self._apply_style()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self._apply_style()
        super().leaveEvent(event)

    def _apply_style(self):
        if self._selected:
            bg, edge = C.TODAY_BG, f"border:1px solid {C.ACCENT};border-left:3px solid {C.ACCENT};"
        elif self._hover:
            bg, edge = C.HOVER_BG, f"border:1px solid {C.BORDER};"
        else:
            bg, edge = C.CARD, f"border:1px solid {C.BORDER};"
        self.setStyleSheet(
            f"QWidget#taskCardV2{{background:{bg};border-radius:8px;{edge}}}"
            "QWidget#taskCardV2 QLabel{background:transparent;border:none;}"
            "QWidget#taskCardV2 QFrame{background:transparent;border:none;}")
