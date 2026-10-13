# -*- coding: utf-8 -*-
"""右栏详情面板（§8.1.5 + §8.8）。

两种状态：
- 空态：今日概览 + 最近到期（≤3 条）+ 新建按钮；
- 详情态：hero 倒计时 + 标题 + explain + 属性行 + detail 全文 +
  历程时间轴 + 右上 × + 底部三键（完成/编辑/更多）。

只渲染 viewmodel.TaskView，不做日期运算；动作一律以信号抛出给 MainWindow。
"""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QHBoxLayout, QVBoxLayout, QFrame,
    QScrollArea, QMenu, QSizePolicy,
)

import viewmodel as vm
from storage import RECUR_LABEL
from style import C


def _lab(text, size=13, weight=400, color=None, wrap=True) -> QLabel:
    # 默认参数在 def 时冻结，故用 None 占位、函数体内运行时取 C
    color = color or C.TEXT_SUB
    lab = QLabel(text)
    f = lab.font()
    f.setPixelSize(size)
    f.setWeight(QFont.Weight(weight))
    lab.setFont(f)
    lab.setStyleSheet(f"color:{color};background:transparent;border:none;")
    lab.setWordWrap(wrap)
    return lab


def _btn(text, primary=False) -> QPushButton:
    b = QPushButton(text)
    b.setFixedHeight(32)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if primary:
        b.setObjectName("primary")
    return b


class _TimelineRow(QWidget):
    """历程时间轴一行：14×14 圆点 + 2px 竖线 | 时间 + 内容。"""

    def __init__(self, when: str, content: str, last: bool):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.setAlignment(Qt.AlignmentFlag.AlignTop)

        rail = QWidget()
        rail.setFixedWidth(22)
        rl = QVBoxLayout(rail)
        rl.setContentsMargins(0, 3, 0, 0)
        rl.setSpacing(0)
        dot = QFrame()
        dot.setFixedSize(14, 14)
        dot.setStyleSheet(
            f"background:{C.ACCENT};border:2px solid {C.CARD};border-radius:7px;")
        rl.addWidget(dot, 0, Qt.AlignmentFlag.AlignHCenter)
        if not last:
            line = QFrame()
            line.setFixedWidth(2)
            line.setStyleSheet(f"background:{C.BORDER};border:none;")
            rl.addWidget(line, 1, Qt.AlignmentFlag.AlignHCenter)
        else:
            rl.addStretch(1)
        lay.addWidget(rail)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 10)
        col.setSpacing(2)
        col.addWidget(_lab(when, 12, 500))
        col.addWidget(_lab(content, 13, 400, C.TEXT))
        lay.addLayout(col, 1)


class DetailPanel(QWidget):
    action_requested = pyqtSignal(str, int)   # 见 MainWindow._on_detail_action
    select_requested = pyqtSignal(int)        # 空态"最近到期"点击
    new_requested = pyqtSignal()
    closed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("detailPanel")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.body = QWidget()
        self.body_lay = QVBoxLayout(self.body)
        self.body_lay.setContentsMargins(20, 16, 20, 16)
        self.body_lay.setSpacing(10)
        self.scroll.setWidget(self.body)
        root.addWidget(self.scroll, 1)

        # 底部操作栏（详情态显示）
        self.action_bar = QWidget()
        self.action_bar.setObjectName("detailActions")
        ab = QHBoxLayout(self.action_bar)
        ab.setContentsMargins(16, 12, 16, 12)
        ab.setSpacing(8)
        self.btn_main = _btn("", primary=True)
        self.btn_main.clicked.connect(self._on_main)
        self.btn_edit = _btn("编辑")
        self.btn_edit.clicked.connect(self._on_edit)
        self.btn_more = _btn("⋯")
        self.btn_more.setFixedWidth(40)
        self.btn_more.clicked.connect(self._on_more)
        ab.addWidget(self.btn_main, 1)
        ab.addWidget(self.btn_edit)
        ab.addWidget(self.btn_more)
        root.addWidget(self.action_bar)

        self._view: vm.TaskView | None = None
        self.show_empty([])

    # ---------------- 状态切换 ----------------

    def _clear(self):
        # 递归清理：body_lay 里既有直接挂的 widget，也有 addLayout 进来的
        # 子布局（标题行 head），后者必须下钻删除内部控件，否则鬼影叠加
        self._clear_lay(self.body_lay)

    def _clear_lay(self, lay):
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
                continue
            sub = item.layout()
            if sub is not None:
                self._clear_lay(sub)
                sub.deleteLater()

    def show_empty(self, views: list):
        """§8.8 空态：今日概览 + 最近到期 ≤3 条 + 新建按钮。"""
        self._view = None
        self.action_bar.hide()
        self._clear()

        pending = [v for v in views if v.status != "done"]
        n_today = sum(1 for v in pending if v.task_type != "long" and v.days == 0)
        n_overdue = sum(1 for v in pending
                        if v.task_type != "long" and v.days is not None and v.days < 0)
        n_long = sum(1 for v in pending if v.task_type == "long")

        card = QFrame()
        card.setObjectName("overviewCard")
        card.setStyleSheet(
            f"QFrame#overviewCard{{background:{C.CARD};border:1px solid {C.BORDER};"
            "border-radius:8px;}")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(6)
        cl.addWidget(_lab("今日概览", 14, 600, C.TEXT))
        cl.addWidget(_lab(f"今天到期 {n_today} · 逾期 {n_overdue} · 长期 {n_long}",
                          13, 400))
        self.body_lay.addWidget(card)

        # 最近到期：逾期组 + 今天组前 3（§8.8）
        upcoming = [v for v in pending
                    if v.group in (vm.GROUP_OVERDUE, vm.GROUP_TODAY)]
        upcoming.sort(key=lambda v: (v.days if v.days is not None else 9999, v.id))
        if upcoming:
            self.body_lay.addWidget(_lab("最近到期", 12, 600))
            for v in upcoming[:3]:
                b = QPushButton(f"{v.title}　·　{v.cd_text}{v.cd_unit}")
                b.setFixedHeight(34)
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                b.setStyleSheet("text-align:left;padding:0 10px;")
                vid = v.id
                b.clicked.connect(lambda _=False, i=vid: self.select_requested.emit(i))
                self.body_lay.addWidget(b)

        nb = _btn("＋ 新建任务", primary=True)
        nb.setFixedHeight(36)
        nb.clicked.connect(self.new_requested.emit)
        self.body_lay.addWidget(nb)
        self.body_lay.addStretch(1)

    def show_view(self, v: vm.TaskView, journals: list):
        """§8.1.5 详情态。"""
        self._view = v
        self.action_bar.show()
        self._clear()

        # 顶部：标题 + ×（热区 40×40）
        head = QHBoxLayout()
        head.setSpacing(4)
        title = _lab(v.title, 16, 600, C.TEXT)
        title.setCursor(Qt.CursorShape.PointingHandCursor)
        title.setToolTip("点击编辑")
        title.mousePressEvent = lambda e: self._on_edit()
        head.addWidget(title, 1)
        x = QPushButton("×")
        x.setFixedSize(40, 40)
        x.setCursor(Qt.CursorShape.PointingHandCursor)
        x.setToolTip("收起详情（Esc）")
        x.setStyleSheet(
            f"QPushButton{{border:none;background:transparent;color:{C.TEXT_SUB};"
            "font-size:18px;font-weight:700;}"
            f"QPushButton:hover{{color:{C.ACCENT};background:{C.HOVER_BG};"
            "border-radius:8px;}}")
        x.clicked.connect(self.closed.emit)
        head.addWidget(x)
        self.body_lay.addLayout(head)

        # hero 倒计时块（有日期者）
        if v.eff_date is not None:
            hero = QFrame()
            hero.setObjectName("heroCard")
            hero.setStyleSheet(
                f"QFrame#heroCard{{background:{C.TODAY_BG};border:none;"
                "border-radius:8px;}")
            hl = QVBoxLayout(hero)
            hl.setContentsMargins(20, 20, 20, 20)
            hl.setSpacing(4)
            n = len(v.cd_text)
            size = 48 if n <= 3 else (32 if n <= 6 else 24)
            big = _lab(v.cd_text + (f" {v.cd_unit}" if v.cd_unit else ""),
                       size, 700, C.ACCENT)
            big.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hl.addWidget(big)
            sub = v.badge[0] if v.badge else ("天后到期" if v.days and v.days > 0 else "")
            if sub:
                s = _lab(sub, 16, 500, C.ACCENT)
                s.setAlignment(Qt.AlignmentFlag.AlignCenter)
                hl.addWidget(s)
            d = _lab(f"目标日 {v.eff_date.isoformat()}"
                     + (f" {v.due_time}" if v.due_time else ""), 13, 400)
            d.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hl.addWidget(d)
            self.body_lay.addWidget(hero)

        # 完整日期语义行（explain 原样）
        self.body_lay.addWidget(_lab(v.explain, 13, 400, C.TEXT))

        # 属性行：类型 / 重复 / 提前预警
        kind = {"short": "短期", "goal": "长期 · 目标", "habit": "长期 · 习惯"}.get(
            v.display_kind if v.task_type == "long" else "short", "短期")
        props = [kind, f"重复：{RECUR_LABEL.get(v.recur, v.recur)}"]
        if v.lead_days:
            props.append(f"提前预警：{v.lead_days} 天")
        if v.task_type == "long" and v.display_kind == "habit":
            props.append(f"连续打卡 {v.streak} 天 · 历程 {v.journal_count} 条")
        self.body_lay.addWidget(_lab("　·　".join(props), 12, 400))

        # detail 全文
        if v.detail:
            det = QFrame()
            det.setObjectName("detailBox")
            det.setStyleSheet(
                f"QFrame#detailBox{{background:{C.CARD};border:1px solid {C.BORDER};"
                "border-radius:8px;}")
            dl = QVBoxLayout(det)
            dl.setContentsMargins(12, 10, 12, 10)
            dl.addWidget(_lab(v.detail, 13, 400, C.TEXT))
            self.body_lay.addWidget(det)

        # 历程时间轴
        if journals:
            self.body_lay.addWidget(_lab(f"历程（{len(journals)}）", 12, 600))
            for i, j in enumerate(journals):
                ts = (j.get("created_at") or "")[:16].replace("-", "/")
                self.body_lay.addWidget(
                    _TimelineRow(ts, j.get("content") or "",
                                 last=(i == len(journals) - 1)))
        self.body_lay.addStretch(1)

        # 主按钮语义（§6.3）
        if v.status == "done":
            self.btn_main.setText("重新打开")
            self.btn_main.setEnabled(True)
        elif v.task_type == "long" and v.display_kind == "habit":
            self.btn_main.setText("今日已打卡 ✓" if v.checked_today else "今日打卡")
            self.btn_main.setEnabled(not v.checked_today)
        elif v.task_type == "long":
            self.btn_main.setText("记一笔")
            self.btn_main.setEnabled(True)
        else:
            self.btn_main.setText("完成本次" if v.recur != "none" else "标记完成")
            self.btn_main.setEnabled(True)

    # ---------------- 动作 ----------------

    def _emit(self, name: str):
        if self._view is not None:
            self.action_requested.emit(name, self._view.id)

    def _on_main(self):
        v = self._view
        if v is None:
            return
        if v.status == "done":
            self._emit("reopen")
        elif v.task_type == "long" and v.display_kind == "habit":
            self._emit("checkin")
        elif v.task_type == "long":
            self._emit("note")
        else:
            self._emit("complete")

    def _on_edit(self):
        self._emit("edit")

    def _on_more(self):
        v = self._view
        if v is None:
            return
        m = QMenu(self)
        acts = {}
        if v.status != "done" and v.eff_date is not None:
            sub = m.addMenu("推迟…")
            acts[sub.addAction("明天")] = "postpone:1"
            acts[sub.addAction("+3 天")] = "postpone:3"
            acts[sub.addAction("下周")] = "postpone:7"
        acts[m.addAction("复制标题")] = "copy_title"
        if v.eff_date is not None:
            acts[m.addAction("跳转到期日")] = "goto_date"
        if v.task_type == "long" and v.status != "done":
            label = "标记完成（结束习惯）" if v.display_kind == "habit" \
                else "标记完成（结束目标）"
            acts[m.addAction(label)] = "finish"
        m.addSeparator()
        acts[m.addAction("删除")] = "delete"
        chosen = m.exec(self.btn_more.mapToGlobal(
            self.btn_more.rect().bottomLeft()))
        if chosen in acts:
            self._emit(acts[chosen])
