# -*- coding: utf-8 -*-
"""自绘迷你月历 MiniCalendar（§8.1.3，弃用 QCalendarWidget 路线）。

- 单元格 26×34、7 列、行列间距 3px、圆角 4、1px border、surface 底；
  hover surface-2；今天 brand 底 + 白字；选中格 accent 描边；
- 圆点 4×4，颜色按当日最紧急档（与 tasks_on 同源）：
  全完成→灰 DONE_DOT；有逾期→红 URGENT_FG；今天起 7 天内→橙 NEAR_FG；
  有重复命中且无更紧急→绿 RECUR_DOT；其余→brand ACCENT；
- 头部：月份标题 + ‹ › 翻月；方向键 ←/→ 翻月（窗口快捷键级）。
"""
import calendar as _cal
from datetime import date

from PyQt6.QtCore import Qt, QDate, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QBrush, QFont
from PyQt6.QtWidgets import QWidget, QLabel, QPushButton, QHBoxLayout, QVBoxLayout

import storage
from storage import STATUS_DONE, RECUR_NONE
from style import C

CELL_W, CELL_H, GAP = 26, 34, 3
COLS, ROWS = 7, 6
GRID_W = COLS * CELL_W + (COLS - 1) * GAP          # 200
HEAD_H = 16
GRID_H = HEAD_H + GAP + ROWS * CELL_H + (ROWS - 1) * GAP

WEEK_HEAD = ["一", "二", "三", "四", "五", "六", "日"]


def _dot_color(day: date, today: date):
    """当日最紧急着色；无任务返回 None。"""
    tasks = storage.tasks_on(day, include_done=True)
    if not tasks:
        return None
    pending = [t for t in tasks if t.get("status") != STATUS_DONE]
    if not pending:
        return C.DONE_DOT                    # 全完成 → 灰
    if day < today:
        return C.URGENT_FG                   # 有逾期 → 红
    if (day - today).days <= 7:
        return C.NEAR_FG                     # 7 天内 → 橙
    if any(t.get("recur") != RECUR_NONE for t in pending):
        return C.RECUR_DOT                   # 重复命中 → 绿
    return C.ACCENT                          # 默认 → brand


class _Grid(QWidget):
    """6×7 自绘格子；点击命中发 date_clicked(date)。"""

    date_clicked = pyqtSignal(date)
    month_shift = pyqtSignal(int)            # ←/→ 翻月

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(GRID_W, GRID_H)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._year = date.today().year
        self._month = date.today().month
        self._selected = date.today()
        self._hover: date | None = None
        self._today = date.today()
        self._cells: list[tuple[date, object]] = []  # (day, rect)

    # ---- 状态 ----
    def set_month(self, year: int, month: int):
        self._year, self._month = year, month
        self._rebuild_cells()
        self.update()

    def set_selected(self, d: date):
        self._selected = d
        self.update()

    def invalidate(self):
        self._today = date.today()
        self.update()

    def _rebuild_cells(self):
        self._cells = []
        first = date(self._year, self._month, 1)
        start_col = first.weekday()          # 周一=0
        days = _cal.monthrange(self._year, self._month)[1]
        for i in range(days):
            idx = start_col + i
            row, col = divmod(idx, COLS)
            x = col * (CELL_W + GAP)
            y = HEAD_H + GAP + row * (CELL_H + GAP)
            self._cells.append((date(self._year, self._month, i + 1),
                                (x, y)))

    # ---- 事件 ----
    def _day_at(self, pos) -> date | None:
        for d, (x, y) in self._cells:
            if x <= pos.x() < x + CELL_W and y <= pos.y() < y + CELL_H:
                return d
        return None

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            d = self._day_at(e.position())
            if d is not None:
                self._selected = d
                self.update()
                self.date_clicked.emit(d)
                return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        d = self._day_at(e.position())
        if d != self._hover:
            self._hover = d
            self.setCursor(Qt.CursorShape.PointingHandCursor
                           if d else Qt.CursorShape.ArrowCursor)
            self.update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        self._hover = None
        self.update()
        super().leaveEvent(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Left:
            self.month_shift.emit(-1)
            return
        if e.key() == Qt.Key.Key_Right:
            self.month_shift.emit(+1)
            return
        super().keyPressEvent(e)

    # ---- 绘制 ----
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        f = p.font()
        f.setPixelSize(12)
        p.setFont(f)

        # 周标题
        p.setPen(QColor(C.TEXT_SUB))
        for c, name in enumerate(WEEK_HEAD):
            x = c * (CELL_W + GAP)
            p.drawText(x, 0, CELL_W, HEAD_H,
                       Qt.AlignmentFlag.AlignCenter, name)

        today = self._today
        for d, (x, y) in self._cells:
            is_today = d == today
            is_sel = d == self._selected
            is_hover = d == self._hover
            # 底
            if is_today:
                bg, border, fg = QColor(C.ACCENT), QColor(C.ACCENT), QColor("#ffffff")
            elif is_sel:
                bg, border, fg = QColor(C.CARD), QColor(C.ACCENT), QColor(C.TEXT)
            elif is_hover:
                bg, border, fg = QColor(C.HOVER_BG), QColor(C.BORDER), QColor(C.TEXT)
            else:
                bg, border, fg = QColor(C.CARD), QColor(C.BORDER), QColor(C.TEXT)
            p.setPen(QPen(border, 1))
            p.setBrush(QBrush(bg))
            p.drawRoundedRect(x, y, CELL_W, CELL_H, 4, 4)
            # 数字（上）+ 圆点（下），纵向居中 gap 2
            p.setPen(QColor(fg))
            p.drawText(x, y + 3, CELL_W, 18,
                       Qt.AlignmentFlag.AlignCenter, str(d.day))
            dot = _dot_color(d, today)
            if dot is not None:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(QColor(dot)))
                p.drawEllipse(x + CELL_W // 2 - 2, y + CELL_H - 9, 4, 4)
        p.end()


class MiniCalendar(QWidget):
    """头（月份 + ‹ ›）+ 自绘网格。接口与旧 DotCalendar 对齐：
    clicked(QDate) / setSelectedDate(QDate) / selectedDate() / invalidate()。"""

    clicked = pyqtSignal(QDate)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel()
        self.title.setStyleSheet("font-size:13px;font-weight:700;")
        self.btn_prev = self._nav("‹", -1)
        self.btn_next = self._nav("›", +1)
        head.addWidget(self.title, 1)
        head.addWidget(self.btn_prev)
        head.addWidget(self.btn_next)
        lay.addLayout(head)

        self.grid = _Grid()
        self.grid.date_clicked.connect(self._on_day)
        self.grid.month_shift.connect(self.shift_month)
        lay.addWidget(self.grid, 0, Qt.AlignmentFlag.AlignHCenter)

        self._selected = date.today()
        self._sync_title()

    def _nav(self, text: str, delta: int) -> QPushButton:
        b = QPushButton(text)
        b.setFixedSize(24, 24)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet(
            f"QPushButton{{border:1px solid {C.BORDER};border-radius:4px;"
            f"background:{C.CARD};font-size:14px;font-weight:700;padding:0;}}"
            f"QPushButton:hover{{background:{C.HOVER_BG};color:{C.ACCENT};}}")
        b.clicked.connect(lambda _=False, d=delta: self.shift_month(d))
        return b

    def shift_month(self, delta: int):
        y, m = self._selected.year, self._selected.month + delta
        if m > 12:
            y, m = y + 1, 1
        elif m < 1:
            y, m = y - 1, 12
        days = _cal.monthrange(y, m)[1]
        self.setSelectedDate(QDate(y, m, min(self._selected.day, days)))

    def _on_day(self, d: date):
        self._selected = d
        self.clicked.emit(QDate(d.year, d.month, d.day))

    # ---- 兼容接口 ----
    def setSelectedDate(self, qd: QDate):
        self._selected = date(qd.year(), qd.month(), qd.day())
        self.grid.set_selected(self._selected)
        self._sync_title()

    def selectedDate(self) -> QDate:
        d = self._selected
        return QDate(d.year, d.month, d.day)

    def invalidate(self):
        self.grid.invalidate()

    def _sync_title(self):
        self.title.setText(f"{self._selected.year} 年 {self._selected.month} 月")
        self.grid.set_month(self._selected.year, self._selected.month)
        self.grid.set_selected(self._selected)
