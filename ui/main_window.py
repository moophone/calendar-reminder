# -*- coding: utf-8 -*-
"""主窗口 v2（§8.1 三栏：左 240 筛选+月历 / 中栏分组卡片流 / 右 360 详情）。

- 1280×800 启动居中，原生标题栏（§8.1.2 降级案），QSplitter 三栏可拖；
- 中栏 QListWidget + setItemWidget(TaskCardV2)：组头可折叠（ui_collapsed）、
  卡片点击模型 = 卡片 clicked 信号（透传方案，见 cards.py 头注）；
- 五筛选（ui_filter）、三栏宽度（ui_splitter）持久化到 settings；
- 日视图：点月历日期进日视图，Esc/「← 返回全部」/Ctrl+D 退出；
- 搜索框单框双行为：输入即过滤、回车 P0 快速添加（失败转导入）。
"""
import json
from datetime import date, timedelta

from PyQt6.QtCore import Qt, QDate, QTimer, QPoint, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QPen, QBrush, QShortcut
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem, QLineEdit, QDialog, QMenu,
    QMessageBox, QInputDialog, QSplitter, QFrame, QApplication,
    QAbstractItemView, QStyle,
)

import storage
from countdown import countdown  # noqa: F401  （保持与旧模块的依赖面一致）
from storage import STATUS_DONE, STATUS_PENDING
from text_parser import parse_line
import style
from style import C
import viewmodel as vm

from .widgets_common import make_app_icon
from .calendar import MiniCalendar
from .cards import TaskCardV2
from .detail_panel import DetailPanel
from .dialog_task import TaskDialog
from .dialog_import import ImportDialog
from .toast import ToastManager

WEEK_CN = "一二三四五六日"

FILTER_LABEL = {
    vm.FILTER_ALL: "全部待办",
    vm.FILTER_LONG: "长期项目",
    vm.FILTER_WEEK: "7 天内到期",
    vm.FILTER_TODAY: "今天到期",
    vm.FILTER_DONE: "已完成",
}
FILTER_ICON = {
    vm.FILTER_ALL: QStyle.StandardPixmap.SP_FileDialogContentsView,
    vm.FILTER_LONG: QStyle.StandardPixmap.SP_DriveHDIcon,
    vm.FILTER_WEEK: QStyle.StandardPixmap.SP_MediaSeekForward,
    vm.FILTER_TODAY: QStyle.StandardPixmap.SP_ComputerIcon,
    vm.FILTER_DONE: QStyle.StandardPixmap.SP_DialogApplyButton,
}


class _EmptyDay(QWidget):
    """日视图空态：自绘日历 + 虚线圆 + 文案 + 新建按钮（§8.7）。"""

    def __init__(self, day: date, on_new):
        super().__init__()
        self._day = day
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 40, 20, 40)
        lay.setSpacing(10)
        lay.addStretch(1)
        art = _EmptyArt()
        art.setFixedSize(120, 120)
        wrap = QHBoxLayout()
        wrap.addStretch(1)
        wrap.addWidget(art)
        wrap.addStretch(1)
        lay.addLayout(wrap)
        t = QLabel("这一天没有安排")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t.setStyleSheet(f"color:{C.TEXT};font-size:15px;font-weight:600;")
        lay.addWidget(t)
        s = QLabel("可以在这一天新建任务，或返回全部待办")
        s.setAlignment(Qt.AlignmentFlag.AlignCenter)
        s.setStyleSheet(f"color:{C.TEXT_SUB};font-size:12px;")
        lay.addWidget(s)
        b = QPushButton("在这天新建任务")
        b.setObjectName("primary")
        b.setFixedHeight(34)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(on_new)
        bw = QHBoxLayout()
        bw.addStretch(1)
        bw.addWidget(b)
        bw.addStretch(1)
        lay.addLayout(bw)
        lay.addStretch(2)


class _EmptyArt(QWidget):
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect().adjusted(18, 14, -18, -14)
        p.setPen(QPen(QColor(C.BORDER), 2))
        p.setBrush(QBrush(QColor(C.CARD)))
        p.drawRoundedRect(r, 10, 10)
        top = r.adjusted(0, 0, 0, -r.height() + 26)
        p.setBrush(QBrush(QColor(C.TODAY_BG)))
        p.drawRoundedRect(top, 10, 10)
        p.setPen(QPen(QColor(C.TEXT_SUB), 2, Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        d = 34
        p.drawEllipse(self.rect().center().x() - d // 2,
                      self.rect().center().y() - d // 2 + 8, d, d)
        p.end()


class _EmptyAll(QWidget):
    """首启无数据大空态（§8.12）。"""

    def __init__(self, on_new, on_import):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 60, 20, 40)
        lay.setSpacing(10)
        lay.addStretch(1)
        art = _EmptyArt()
        art.setFixedSize(120, 120)
        w = QHBoxLayout()
        w.addStretch(1)
        w.addWidget(art)
        w.addStretch(1)
        lay.addLayout(w)
        t = QLabel("还没有待办事项")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        t.setStyleSheet(f"color:{C.TEXT};font-size:16px;font-weight:700;")
        lay.addWidget(t)
        s = QLabel("新建一条，或把整段安排粘贴进来自动解析")
        s.setAlignment(Qt.AlignmentFlag.AlignCenter)
        s.setStyleSheet(f"color:{C.TEXT_SUB};font-size:12px;")
        lay.addWidget(s)
        row = QHBoxLayout()
        row.addStretch(1)
        b1 = QPushButton("＋ 新建任务")
        b1.setObjectName("primary")
        b1.setFixedHeight(34)
        b1.clicked.connect(on_new)
        b2 = QPushButton("导入文本")
        b2.setFixedHeight(34)
        b2.clicked.connect(on_import)
        for b in (b1, b2):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addStretch(2)


class MainWindow(QWidget):
    data_changed = pyqtSignal()
    theme_changed = pyqtSignal(str)   # §9.3 换肤后通知托盘/悬浮球重绘

    def __init__(self):
        super().__init__()
        self.setObjectName("rootBg")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setWindowTitle("日历提醒")
        self.setWindowIcon(make_app_icon())
        self.setMinimumSize(720, 480)   # 允许拖小：老值 960×600 在小屏上放不下
        scr = QApplication.primaryScreen().availableGeometry()
        # 四周强制留白（逻辑像素），高 DPI 缩放屏也不会贴边/超出
        MARGIN = 40
        # 默认尺寸：屏幕减双边留白（上限 1180×760）
        dw = max(self.minimumWidth(), min(1180, scr.width() - 2 * MARGIN))
        dh = max(self.minimumHeight(), min(760, scr.height() - 2 * MARGIN))
        # 恢复上次关闭时的窗口大小与位置（越界/过大则钳制到安全留白区内）
        geo = self._load_geometry()
        if geo is not None:
            x, y, w, h = geo
            w = max(self.minimumWidth(), min(w, scr.width() - 2 * MARGIN))
            h = max(self.minimumHeight(), min(h, scr.height() - 2 * MARGIN))
            x = max(scr.left(), min(x, scr.right() - MARGIN - w))
            y = max(scr.top(), min(y, scr.bottom() - MARGIN - h))
            self.setGeometry(x, y, w, h)
        else:
            self.resize(dw, dh)
            self.move(scr.center() - self.rect().center())

        self._day: date | None = None          # 日视图日期；None=分组流
        self.selected_task_id: int | None = None
        self._views: list[vm.TaskView] = []
        self._items: dict[int, QListWidgetItem] = {}
        self._syncing = False
        self.toast = ToastManager(self)

        self._filter = storage.get_setting("ui_filter") or vm.FILTER_ALL
        if self._filter not in vm.FILTER_ORDER:
            self._filter = vm.FILTER_ALL
        try:
            self._collapsed = json.loads(
                storage.get_setting("ui_collapsed") or "{}")
        except (TypeError, ValueError):
            self._collapsed = {}

        self._build()
        self._build_shortcuts()
        self.refresh_all()

    # ================= 布局 =================

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_topbar())

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(1)
        self.splitter.addWidget(self._build_left())
        self.splitter.addWidget(self._build_center())
        self.detail = DetailPanel()
        self.detail.setMinimumWidth(300)
        self.detail.setMaximumWidth(420)
        self.detail.action_requested.connect(self._on_action)
        self.detail.select_requested.connect(self.select_task)
        self.detail.new_requested.connect(self.open_new)
        self.detail.closed.connect(self.clear_selection)
        self.splitter.addWidget(self.detail)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        saved = storage.get_setting("ui_splitter")
        sizes = None
        if saved:
            try:
                sizes = [int(x) for x in json.loads(saved)]
                if len(sizes) != 3:
                    sizes = None
            except (TypeError, ValueError):
                sizes = None
        self.splitter.setSizes(sizes or [240, 680, 360])
        self.splitter.splitterMoved.connect(self._save_splitter)
        root.addWidget(self.splitter, 1)

    def _build_topbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("topBar")
        bar.setFixedHeight(56)
        self._topbar = bar
        self._style_topbar()
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(10)

        brand = QWidget()
        brand.setFixedWidth(208)  # 与左栏内容区对齐（240 - 2×16）
        bl = QHBoxLayout(brand)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(8)
        logo = QLabel()
        logo.setPixmap(make_app_icon().pixmap(22, 22))
        bl.addWidget(logo)
        t = QLabel("日历提醒")
        t.setStyleSheet("font-size:16px;font-weight:600;")
        bl.addWidget(t)
        bl.addStretch(1)
        lay.addWidget(brand)

        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索或快速添加，回车解析…")
        self.search.setFixedSize(240, 32)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _=None: self._rebuild_list())
        self.search.returnPressed.connect(self._quick_add)
        lay.addWidget(self.search)

        btn_import = QPushButton("导入文本")
        btn_import.setObjectName("primary")
        btn_import.setFixedHeight(32)
        btn_import.clicked.connect(self.open_import)
        btn_add = QPushButton("新建")
        btn_add.setFixedHeight(32)
        btn_add.clicked.connect(self.open_new)
        lay.addWidget(btn_import)
        lay.addWidget(btn_add)
        lay.addStretch(1)

        self.btn_theme = QPushButton()
        self.btn_theme.setFixedSize(32, 32)
        self.btn_theme.setToolTip("切换主题（Ctrl+T）")
        self.btn_theme.setIcon(self._theme_icon())
        self.btn_theme.clicked.connect(self.open_theme_menu)
        lay.addWidget(self.btn_theme)
        return bar

    def _style_topbar(self):
        self._topbar.setStyleSheet(
            f"QWidget#topBar{{background:{C.CARD};"
            f"border-bottom:1px solid {C.BORDER};}}")

    def _theme_icon(self) -> QIcon:
        pm = QPixmap(16, 16)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QBrush(QColor(C.ACCENT)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(1, 1, 14, 14)
        p.end()
        return QIcon(pm)

    def _build_left(self) -> QWidget:
        left = QWidget()
        left.setMinimumWidth(200)
        left.setMaximumWidth(300)
        lay = QVBoxLayout(left)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)

        self.cal = MiniCalendar()
        self.cal.clicked.connect(self.on_pick_date)
        lay.addWidget(self.cal, 0, Qt.AlignmentFlag.AlignHCenter)

        self.filter_btns: dict[str, QPushButton] = {}
        fbox = QVBoxLayout()
        fbox.setSpacing(2)
        for key in vm.FILTER_ORDER:
            b = QPushButton(f"  {FILTER_LABEL[key]}")
            b.setCheckable(True)
            b.setFixedHeight(36)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setIcon(self.style().standardIcon(FILTER_ICON[key]))
            b.clicked.connect(lambda _=False, k=key: self._set_filter(k))
            self.filter_btns[key] = b
            fbox.addWidget(b)
        lay.addLayout(fbox)
        lay.addStretch(1)
        return left

    def _build_center(self) -> QWidget:
        center = QWidget()
        lay = QVBoxLayout(center)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(8)

        # 日视图日期条（默认隐藏）
        self.day_bar = QWidget()
        db = QHBoxLayout(self.day_bar)
        db.setContentsMargins(0, 0, 0, 0)
        self.day_label = QLabel()
        self.day_label.setStyleSheet("font-size:15px;font-weight:700;")
        db.addWidget(self.day_label, 1)
        back = QPushButton("← 返回全部")
        back.setFixedHeight(28)
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.clicked.connect(self.exit_day_view)
        db.addWidget(back)
        self.day_bar.hide()
        lay.addWidget(self.day_bar)

        self.list = QListWidget()
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.currentItemChanged.connect(self._on_current_changed)
        self.list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        self.list.installEventFilter(self)
        lay.addWidget(self.list, 1)
        return center

    # ================= 筛选 / 持久化 =================

    def _set_filter(self, key: str):
        # 再点同一项回到「全部」（§8.1.3）
        self._filter = vm.FILTER_ALL if (key == self._filter
                                         and key != vm.FILTER_ALL) else key
        storage.set_setting("ui_filter", self._filter)
        self._rebuild_list()

    def _save_splitter(self):
        storage.set_setting("ui_splitter",
                            json.dumps(self.splitter.sizes()))

    def _load_geometry(self):
        """上次保存的窗口几何 (x, y, w, h)；无记录或损坏返回 None。"""
        raw = storage.get_setting("ui_geometry")
        if not raw:
            return None
        try:
            geo = [int(v) for v in json.loads(raw)]
            if len(geo) != 4:
                return None
            return geo
        except (TypeError, ValueError):
            return None

    def _save_geometry(self):
        g = self.geometry()
        storage.set_setting("ui_geometry",
                            json.dumps([g.x(), g.y(), g.width(), g.height()]))

    def _save_collapsed(self):
        storage.set_setting("ui_collapsed", json.dumps(self._collapsed))

    def _style_filter_btns(self, counts: dict):
        for key, b in self.filter_btns.items():
            n = counts.get(key, 0)
            b.setText(f"  {FILTER_LABEL[key]}　{n}")
            b.setChecked(key == self._filter)
            if key == self._filter:
                b.setStyleSheet(
                    f"text-align:left;padding:0 12px;border:none;border-radius:4px;"
                    f"background:{C.TODAY_BG};color:{C.ACCENT};font-weight:600;")
            elif n == 0:
                b.setStyleSheet(
                    f"text-align:left;padding:0 12px;border:none;border-radius:4px;"
                    f"background:transparent;color:{C.TEXT_SUB};")
            else:
                b.setStyleSheet(
                    f"text-align:left;padding:0 12px;border:none;border-radius:4px;"
                    f"background:transparent;color:{C.TEXT};")

    # ================= 数据与列表 =================

    @property
    def current_day(self) -> date:
        return self._day or date.today()

    def refresh_all(self):
        self._views = vm.build_views()
        self.cal.invalidate()
        self._rebuild_list()
        self.data_changed.emit()

    def refresh_panel(self):   # main.py 兼容锚点
        self.refresh_all()

    def _visible_views(self) -> list[vm.TaskView]:
        q = self.search.text().strip().lower()
        if not q:
            return self._views
        return [v for v in self._views
                if q in v.title.lower() or q in (v.detail or "").lower()]

    def _rebuild_list(self):
        self._syncing = True
        try:
            self.list.clear()
            self._items = {}
            views = self._visible_views()

            if self._day is not None:
                self._fill_day_views()
            else:
                self.day_bar.hide()
                self._fill_groups(views)

            self._style_filter_btns(vm.filter_counts(self._views))
            self._restore_selection()
        finally:
            self._syncing = False

    def _fill_groups(self, views: list):
        if not views:
            item = QListWidgetItem()
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            w = _EmptyAll(self.open_new, self.open_import)
            item.setSizeHint(w.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, w)
            return
        groups = vm.group_views(views, self._filter)
        if not groups:
            item = QListWidgetItem()
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            lab = QLabel(f"「{FILTER_LABEL[self._filter]}」下暂无事项")
            lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lab.setStyleSheet(f"color:{C.TEXT_SUB};padding:40px 0;")
            item.setSizeHint(lab.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, lab)
            return
        for gkey, items in groups:
            self._add_group_header(gkey, len(items))
            if not self._collapsed.get(gkey, False):
                for v in items:
                    self._add_task_item(v)

    def _add_group_header(self, gkey: str, count: int):
        item = QListWidgetItem()
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)  # 不可选
        collapsed = self._collapsed.get(gkey, False)
        arrow = "▸" if collapsed else "▾"
        b = QPushButton(f"{arrow}  {vm.GROUP_LABEL[gkey]}　{count}")
        b.setFixedHeight(30)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet(
            f"text-align:left;border:none;background:transparent;"
            f"color:{C.TEXT_SUB};font-size:12px;font-weight:700;padding:0 4px;")
        b.clicked.connect(lambda _=False, k=gkey: self._toggle_group(k))
        item.setSizeHint(b.sizeHint())
        self.list.addItem(item)
        self.list.setItemWidget(item, b)

    def _toggle_group(self, gkey: str):
        self._collapsed[gkey] = not self._collapsed.get(gkey, False)
        self._save_collapsed()
        self._rebuild_list()

    def _add_task_item(self, v: vm.TaskView):
        card = TaskCardV2(v)
        card.clicked.connect(self.select_task)
        card.action_requested.connect(self._on_action)
        item = QListWidgetItem()
        item.setSizeHint(card.sizeHint())
        self.list.addItem(item)
        self.list.setItemWidget(item, card)
        self._items[v.id] = item

    def _fill_day_views(self):
        day = self._day
        views = vm.build_views_for_day(day)
        wd = WEEK_CN[day.weekday()]
        self.day_label.setText(
            f"{day.year} 年 {day.month} 月 {day.day} 日 周{wd} · {len(views)} 项")
        self.day_bar.show()
        if not views:
            item = QListWidgetItem()
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            w = _EmptyDay(day, lambda: self.open_new(default_date=day))
            item.setSizeHint(w.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, w)
            return
        for v in views:
            self._add_task_item(v)

    def _restore_selection(self):
        sid = self.selected_task_id
        item = self._items.get(sid) if sid else None
        if item is not None:
            self.list.setCurrentItem(item)
        v = next((v for v in self._views if v.id == sid), None)
        if v is not None:
            self.detail.show_view(v, storage.journals_of(v.id))
        else:
            self.selected_task_id = None
            self.detail.show_empty(self._views)
        self._apply_card_selection()

    def _apply_card_selection(self):
        for tid, item in self._items.items():
            w = self.list.itemWidget(item)
            if isinstance(w, TaskCardV2):
                w.set_selected(tid == self.selected_task_id)

    # ================= 选中与键盘 =================

    def select_task(self, task_id: int):
        self.selected_task_id = task_id
        item = self._items.get(task_id)
        if item is not None and self.list.currentItem() is not item:
            self._syncing = True
            try:
                self.list.setCurrentItem(item)
            finally:
                self._syncing = False
        self._apply_card_selection()
        v = next((v for v in self._views if v.id == task_id), None)
        if v is not None:
            self.detail.show_view(v, storage.journals_of(task_id))

    def clear_selection(self):
        self.selected_task_id = None
        self.list.clearSelection()
        self._apply_card_selection()
        self.detail.show_empty(self._views)

    def _on_current_changed(self, cur, _prev):
        if self._syncing or cur is None:
            return
        for tid, item in self._items.items():
            if item is cur:
                self.select_task(tid)
                return

    def eventFilter(self, obj, e):
        if obj is self.list and e.type() == e.Type.KeyPress:
            key = e.key()
            v = self._selected_view()
            if key == Qt.Key.Key_Space and v is not None:
                self._space_action(v)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and v is not None:
                self._act_edit(v)
                return True
            if key == Qt.Key.Key_Delete and v is not None:
                self._act_delete(v)
                return True
            if key == Qt.Key.Key_Menu and v is not None:
                self._open_task_menu(v, self.list.viewport().rect().center())
                return True
        return super().eventFilter(obj, e)

    def _selected_view(self) -> vm.TaskView | None:
        if self.selected_task_id is None:
            return None
        return next((v for v in self._views
                     if v.id == self.selected_task_id), None)

    def _space_action(self, v: vm.TaskView):
        if v.status == "done":
            self._act_reopen(v)
        elif v.task_type == "long" and v.display_kind == "habit":
            self._act_checkin(v)
        elif v.task_type == "long":
            self._act_note(v)
        else:
            self._act_complete(v)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            if self.search.text():
                self.search.clear()
            elif self._day is not None:
                self.exit_day_view()
            else:
                self.clear_selection()
            return
        super().keyPressEvent(e)

    def _build_shortcuts(self):
        def sc(key, fn):
            s = QShortcut(QKeySequence(key), self)
            s.setContext(Qt.ShortcutContext.WindowShortcut)
            s.activated.connect(fn)
        sc("Ctrl+N", self.open_new)
        sc("Ctrl+I", self.open_import)
        sc("Ctrl+F", lambda: (self.search.setFocus(), self.search.selectAll()))
        sc("Ctrl+D", self.go_today)
        sc("Ctrl+T", self.open_theme_menu)

    # ================= 动作（卡片/右栏/右键统一入口） =================

    def _on_action(self, name: str, task_id: int):
        v = next((v for v in self._views if v.id == task_id), None)
        if v is None:
            return
        self.selected_task_id = task_id
        if name == "complete":
            self._act_complete(v)
        elif name == "checkin":
            self._act_checkin(v)
        elif name == "note":
            self._act_note(v)
        elif name == "reopen":
            self._act_reopen(v)
        elif name == "edit":
            self._act_edit(v)
        elif name == "delete":
            self._act_delete(v)
        elif name == "finish":
            self._act_finish(v)
        elif name == "copy_title":
            QApplication.clipboard().setText(v.title)
            self.toast.show("标题已复制")
        elif name == "goto_date":
            self._act_goto(v)
        elif name.startswith("postpone:"):
            self._act_postpone(v, int(name.split(":")[1]))

    def _act_complete(self, v: vm.TaskView):
        storage.complete_task(v.id, date.today())
        self.refresh_all()
        if v.recur != "none":
            nv = next((x for x in self._views if x.id == v.id), None)
            if nv is not None and nv.eff_date is not None:
                self.toast.show(
                    f"已推进到 {nv.eff_date.month}月{nv.eff_date.day}日")
                return
        self.toast.show("已完成 · 可在已完成中撤销")

    def _act_checkin(self, v: vm.TaskView):
        if vm.has_checkin_today(v.id):
            self.toast.show("今天已打过卡")
            return
        storage.add_journal(v.id, "打卡")
        self.refresh_all()
        nv = next((x for x in self._views if x.id == v.id), None)
        self.toast.show(
            f"打卡成功 · 连续 {nv.streak} 天" if nv else "打卡成功")

    def _act_note(self, v: vm.TaskView):
        text, ok = QInputDialog.getMultiLineText(
            self, "记一笔", v.title)
        if ok and text.strip():
            storage.add_journal(v.id, text.strip())
            self.refresh_all()
            self.toast.show("已记入历程")

    def _act_reopen(self, v: vm.TaskView):
        storage.reopen_task(v.id)
        self.refresh_all()
        self.toast.show("已重新打开")

    def _act_edit(self, v: vm.TaskView):
        t = storage.get_task(v.id)
        if not t:
            return
        dlg = TaskDialog(self, task=t)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            storage.update_task(t["id"], **dlg.get_data())
            self.refresh_all()

    def _act_delete(self, v: vm.TaskView):
        r = QMessageBox.question(
            self, "删除确认",
            f"确定删除「{v.title}」吗？\n该操作的历程记录会一并删除，不可恢复。")
        if r == QMessageBox.StandardButton.Yes:
            storage.delete_task(v.id)
            self.selected_task_id = None
            self.refresh_all()
            self.toast.show("已删除")

    def _act_finish(self, v: vm.TaskView):
        r = QMessageBox.question(
            self, "结束长期项",
            f"确定将「{v.title}」整体标记完成吗？\n"
            "结束后不再每日提醒，可在「已完成」中重新打开。")
        if r == QMessageBox.StandardButton.Yes:
            storage.complete_task(v.id, date.today())
            self.refresh_all()
            self.toast.show("已结束，可在已完成中查看")

    def _act_postpone(self, v: vm.TaskView, days: int):
        base = v.eff_date or date.today()
        new_d = base + timedelta(days=days)
        storage.update_task(v.id, due_date=new_d.isoformat())
        self.refresh_all()
        self.toast.show(f"已推迟到 {new_d.month}月{new_d.day}日")

    def _act_goto(self, v: vm.TaskView):
        if v.eff_date is None:
            return
        d = v.eff_date
        self.cal.setSelectedDate(QDate(d.year, d.month, d.day))
        self._day = d
        self._rebuild_list()
        self.select_task(v.id)

    # ================= 右键菜单（§8.10） =================

    def _context_menu(self, pos: QPoint):
        item = self.list.itemAt(pos)
        if item is None:
            return
        for tid, it in self._items.items():
            if it is item:
                self.select_task(tid)
                v = next((x for x in self._views if x.id == tid), None)
                if v is not None:
                    self._open_task_menu(
                        v, self.list.viewport().mapToGlobal(pos))
                return

    def _open_task_menu(self, v: vm.TaskView, global_pos: QPoint):
        m = QMenu(self)
        acts = {}
        if v.status == "done":
            acts[m.addAction("重新打开")] = "reopen"
            acts[m.addAction("永久删除")] = "delete"
        else:
            acts[m.addAction("编辑")] = "edit"
            if v.task_type == "long" and v.display_kind == "habit":
                acts[m.addAction("今日打卡")] = "checkin"
            elif v.task_type == "long":
                acts[m.addAction("记一笔")] = "note"
            else:
                acts[m.addAction(
                    "完成本次" if v.recur != "none" else "完成")] = "complete"
            acts[m.addAction("复制标题")] = "copy_title"
            if v.eff_date is not None:
                acts[m.addAction("跳转到期日")] = "goto_date"
            m.addSeparator()
            acts[m.addAction("删除")] = "delete"
        chosen = m.exec(global_pos)
        if chosen in acts:
            self._on_action(acts[chosen], v.id)

    # ================= 日视图 / 月历 =================

    def on_pick_date(self, qd: QDate):
        self._day = date(qd.year(), qd.month(), qd.day())
        self._rebuild_list()

    def exit_day_view(self):
        self._day = None
        self._rebuild_list()

    def go_today(self):
        self._day = None
        self.cal.setSelectedDate(QDate.currentDate())
        self.refresh_all()

    # ================= 顶栏动作 =================

    def open_new(self, default_date: date | None = None):
        dlg = TaskDialog(self, default_date=default_date or self.current_day)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            kind = data.pop("display_kind", None)
            new_id = storage.add_task(**data)
            if kind:   # add_task 无 display_kind 参数，走白名单补写（§6.4）
                storage.update_task(new_id, display_kind=kind)
            self.refresh_all()
            self.toast.show("已新建")

    def open_import(self, preset: str = ""):
        dlg = ImportDialog(self, bind_date=self.current_day, preset_text=preset)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            evs = dlg.accepted_events()
            for ev in evs:
                storage.add_task(**ev)
            self.refresh_all()
            if evs:
                self.toast.show(f"已导入 {len(evs)} 条")
                d0 = evs[0].get("due_date")
                if d0:
                    try:
                        d = date.fromisoformat(d0)
                        self.cal.setSelectedDate(QDate(d.year, d.month, d.day))
                    except ValueError:
                        pass

    def _quick_add(self):
        """搜索框回车：P0 规则解析快速添加；失败/多义 → 转导入（§8.1.2）。"""
        text = self.search.text().strip()
        if not text:
            return
        ev = parse_line(text, self.current_day)
        if ev.has_cue and not ev.ambiguous:
            storage.add_task(
                title=ev.title, due_date=ev.due_date, due_time=ev.due_time,
                recur=ev.recur, lead_days=ev.lead_days, detail=ev.detail)
            self.search.clear()
            self.refresh_all()
            self.toast.show(f"已添加：{ev.title}")
        else:
            # 解析不出时间线索 → 转人工确认，原文带进 ImportDialog
            self.open_import(preset=text)
            self.search.clear()

    # ================= 主题（§9） =================

    def open_theme_menu(self):
        """主题按钮 / Ctrl+T：弹出 5 主题菜单（色板圆点 + 中文名 + 当前勾选）。"""
        menu = QMenu(self)
        menu.setMinimumWidth(240)
        cur = style.current_theme()
        for k, pal in style.THEMES.items():
            pm = QPixmap(20, 20)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setBrush(QBrush(QColor(pal["ACCENT"])))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(2, 2, 16, 16)
            p.end()
            a = menu.addAction(QIcon(pm), style.THEME_NAMES[k])
            a.setCheckable(True)
            a.setChecked(k == cur)
            a.triggered.connect(lambda _=False, name=k: self._apply_theme(name))
        menu.addSeparator()
        custom = menu.addAction("自定义皮肤…（后续版本）")
        custom.setEnabled(False)
        menu.exec(self.btn_theme.mapToGlobal(
            QPoint(0, self.btn_theme.height() + 4)))

    def _apply_theme(self, name: str):
        """换肤：QSS + 顶栏 inline 样式重取 C；列表/详情走 refresh_all 全量重刷。"""
        style.apply_theme(QApplication.instance(), name)
        self._style_topbar()
        self.btn_theme.setIcon(self._theme_icon())
        self.refresh_all()          # _rebuild_list → _restore_selection → detail 重显
        self.toast.show(f"已切换到「{style.THEME_NAMES[name]}」主题")
        self.theme_changed.emit(name)

    # ================= 窗口行为 =================

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.toast.on_parent_resize()

    def closeEvent(self, e):
        """关窗口只隐藏，程序由托盘/悬浮球继续常驻。"""
        self._save_splitter()
        self._save_geometry()
        e.ignore()
        self.hide()
