# -*- coding: utf-8 -*-
"""文本导入确认弹窗与 AI 后台线程（纯搬运自 ui.py）。"""
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
from .widgets_common import NoWheelDateEdit


class _AiSelectWorker(QThread):
    """P1 选择器后台线程：逐块调本地模型选句子/候选 ID，不卡界面。"""
    progress = pyqtSignal(int, int)        # 已完成块, 总块数
    chunk_done = pyqtSignal(int, list)     # 块序号, AI 行列表
    finished_all = pyqtSignal(bool)        # 是否整体成功

    def __init__(self, pipeline, chunks, parent=None):
        super().__init__(parent)
        self._pipeline = pipeline
        self._chunks = chunks              # list[list[Sentence]]
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        ok_any = False
        try:
            for k, ch in enumerate(self._chunks):
                if self._cancel:
                    self.finished_all.emit(False)
                    return
                try:
                    rows = llm_fallback.extract_selection(
                        ch, self._pipeline.date_cands,
                        self._pipeline.time_cands, self._pipeline.recur_cands,
                        chunk_idx=k)
                except Exception:
                    rows = None
                if rows:
                    ok_any = True
                    self.chunk_done.emit(k, rows)
                self.progress.emit(k + 1, len(self._chunks))
            self.finished_all.emit(ok_any)
        except Exception:
            self.finished_all.emit(False)


class ImportDialog(QDialog):
    """粘贴文本 → 智能解析 → 表格确认（可改日期/时间/重复/类型、取消勾选）→ 入库。

    解析管线（纯本地）：
    - P0 规则先行：毫秒级上表，日期/时间/重复全部由算法算出；
    - P1 AI 选择器（可选）：本地模型只做分句分组、起标题、选候选 ID；
    - P2/P3 合一终检：句子重叠配对合并，AI 独有两档（黄底自动勾选/红底需人工确认）。
    勾选"强制绑定日期"后，整批事项统一落在日历当前选中的日子。
    """

    def __init__(self, parent=None, bind_date: date | None = None,
                 preset_text: str = ""):
        super().__init__(parent)
        self.setWindowTitle("导入文本")
        self.resize(860, 660)
        # §8.3：最大高 80% 屏
        self.setMaximumHeight(
            int(QApplication.primaryScreen().availableGeometry().height() * 0.8))
        self._preset_text = preset_text   # 快速添加转入时预填原文（§8.1.2）
        self._pipeline = None       # P0 结果（PipelineResult）
        self._user_edited = set()   # {(行号, 字段)}：AI 合并时跳过用户改过的字段
        self._ai_rows = []          # 各块 AI 行累计
        self._ai_worker = None
        self._n_chunks = 0
        self._rule_count = 0        # 表格前 N 行为规则行（其后为 AI 独有）
        self.events = []            # 当前表格行对应的事件

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(10)

        tip = QLabel("把通知、赛事信息、聊天记录整段粘进来，点“智能解析”。"
                     "规则结果立即上表；本地模型随后在后台优化分组与标题备注。"
                     "黄底=AI 新增请复核，红底=AI 新增需人工确认：")
        tip.setStyleSheet(f"color:{C.TEXT_SUB};")
        tip.setWordWrap(True)
        lay.addWidget(tip)

        # —— 强制绑定日期 ——
        bind_row = QHBoxLayout()
        self.chk_bind = QCheckBox("强制绑定到日期：")
        self.chk_bind.setChecked(True)
        self.bind_edit = NoWheelDateEdit()
        self.bind_edit.setCalendarPopup(True)
        self.bind_edit.setDisplayFormat("yyyy-MM-dd")
        bd = bind_date or date.today()
        self._fallback_qd = QDate(bd.year, bd.month, bd.day)
        self.bind_edit.setDate(QDate(bd.year, bd.month, bd.day))
        self.btn_today = QPushButton("今天")
        self.btn_today.setFixedWidth(52)
        self.btn_today.clicked.connect(
            lambda: self.bind_edit.setDate(QDate.currentDate()))
        self.bind_hint = QLabel(ImportDialog._BIND_HINT_NORMAL)
        self.bind_hint.setStyleSheet(f"color:{C.TEXT_SUB};")
        self.bind_hint.setWordWrap(True)
        bind_row.addWidget(self.chk_bind)
        bind_row.addWidget(self.bind_edit)
        bind_row.addWidget(self.btn_today)
        bind_row.addWidget(self.bind_hint, 1)
        lay.addLayout(bind_row)

        self.input = QTextEdit()
        self.input.setPlaceholderText(
            "例如：\n10月10号提交互联网+软著材料，提前3天\n下周五下午3点半项目答辩\n每天背单词\n每月15号还花呗\n\n"
            "赛事/通知这类一大段说明，直接整段粘进来，会自动拆分事项并提炼标题备注。"
        )
        self.input.setFixedHeight(150)
        if self._preset_text:
            self.input.setPlainText(self._preset_text)
        lay.addWidget(self.input)

        row = QHBoxLayout()
        self.parse_btn = QPushButton("智能解析")
        self.parse_btn.setObjectName("primary")
        self.parse_btn.clicked.connect(self.do_parse)
        self.warn_lab = QLabel("")
        self.warn_lab.setStyleSheet(f"color:{C.WARN}; font-weight:600;")
        self.warn_lab.setWordWrap(True)
        row.addWidget(self.parse_btn)
        row.addWidget(self.warn_lab, 1)
        lay.addLayout(row)

        sel_row = QHBoxLayout()
        sel_row.addWidget(QLabel("勾选："))
        self.btn_all = QPushButton("全选")
        self.btn_all.setFixedWidth(64)
        self.btn_none = QPushButton("全不选")
        self.btn_none.setFixedWidth(72)
        sel_row.addWidget(self.btn_all)
        sel_row.addWidget(self.btn_none)
        sel_row.addStretch(1)
        lay.addLayout(sel_row)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["导入", "日期", "时间", "重复", "类型", "事项"])
        self.table.horizontalHeader().setSectionResizeMode(
            5, QHeaderView.ResizeMode.Stretch)
        for i, w in enumerate([48, 130, 80, 90, 76, 1]):
            if w > 1:
                self.table.setColumnWidth(i, w)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.AllEditTriggers)
        lay.addWidget(self.table, 1)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("确认导入")
        ok.setObjectName("primary")
        ok.setDefault(True)   # Enter = 确认导入（全局交互规范 §6）
        ok.clicked.connect(self.accept)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        lay.addLayout(btns)

        self.chk_bind.toggled.connect(self._apply_bind)
        self.bind_edit.dateChanged.connect(self._apply_bind)
        self.table.itemChanged.connect(self._on_item_changed)
        self.btn_all.clicked.connect(lambda: self._set_all_checked(True))
        self.btn_none.clicked.connect(lambda: self._set_all_checked(False))

    def _set_all_checked(self, on: bool):
        for r in range(self.table.rowCount()):
            w = self.table.cellWidget(r, 0)
            chk = w.findChild(QCheckBox) if w else None
            if chk is not None:
                chk.setChecked(on)

    def _on_item_changed(self, item):
        if item.column() == 5:
            self._user_edited.add((item.row(), "title"))

    def _edited_by_row(self) -> dict:
        d: dict = {}
        for row, field in self._user_edited:
            d.setdefault(row, set()).add(field)
        return d

    @staticmethod
    def _parse_time(t: str) -> str | None:
        """简单容错：3 → 03:00，3:30 原样，非法清空。"""
        t = (t or "").strip()
        if not t or t == "无":
            return None
        try:
            parts = t.replace("：", ":").split(":")
            hh = int(parts[0])
            mm = int(parts[1]) if len(parts) > 1 else 0
            if 0 <= hh <= 23 and 0 <= mm <= 59:
                return f"{hh:02d}:{mm:02d}"
        except (ValueError, IndexError):
            pass
        return None

    def _apply_bind_decision(self, events, split_mode: bool):
        """文本里有多个互不相同且明确的日期时不统一绑定；否则绑最近/兜底日期。"""
        clear_dates = sorted({e.due_date for e in events
                              if e.due_date and not e.ambiguous})
        if split_mode and len(clear_dates) > 1:
            bind_on, qd = False, None
        elif clear_dates:
            y, m, d = map(int, clear_dates[0].split("-"))
            bind_on, qd = True, QDate(y, m, d)
        else:
            bind_on, qd = True, None
        if qd is None:
            qd = self._fallback_qd
        self.chk_bind.blockSignals(True)
        self.bind_edit.blockSignals(True)
        self.chk_bind.setChecked(bind_on)
        self.bind_edit.setDate(qd)
        self.bind_edit.blockSignals(False)
        self.chk_bind.blockSignals(False)

    def do_parse(self):
        w = self._ai_worker
        if w is not None and w.isRunning():
            w.cancel()                     # 运行中再点 = 取消 AI
            self.parse_btn.setEnabled(False)
            self.warn_lab.setText("正在取消 AI…")
            return
        text = self.input.toPlainText().strip()
        if not text:
            return
        self._user_edited = set()
        self._ai_rows = []
        self._pipeline = parse_pipeline(text, date.today())
        self._rule_count = len(self._pipeline.events)
        self.events = list(self._pipeline.events)
        self._fill_table()
        self._apply_bind_decision(
            self.events,
            split_mode=len({e.due_date for e in self.events
                            if e.due_date and not e.ambiguous}) > 1)
        # P0 已秒出；有本地模型时 AI 在后台做分组/标题/备注优化
        if llm_fallback.is_available():
            self._start_ai()

    # ---------- P1 AI 选择器 ----------
    def _start_ai(self):
        chunks = llm_fallback.chunk_sentences(self._pipeline.sentences)
        self._n_chunks = len(chunks)
        self.parse_btn.setText("取消 AI")
        self.warn_lab.setText(
            f"AI 解析中 0/{self._n_chunks}"
            "（规则结果已可用；首次使用需加载模型，约几秒）…")
        self._ai_worker = _AiSelectWorker(self._pipeline, chunks, self)
        self._ai_worker.progress.connect(self._on_ai_progress)
        self._ai_worker.chunk_done.connect(self._on_chunk_done)
        self._ai_worker.finished_all.connect(self._on_ai_finished)
        # 线程回收保护（§8.3）：跑完即 deleteLater，防线程对象泄漏
        self._ai_worker.finished.connect(self._ai_worker.deleteLater)
        self._ai_worker.start()

    def _on_ai_progress(self, done: int, total: int):
        if total > 1:
            self.warn_lab.setText(f"AI 解析中 {done}/{total} …")

    def _on_chunk_done(self, idx: int, rows: list):
        self._ai_rows.extend(rows)

    def _on_ai_finished(self, ok: bool):
        self.parse_btn.setText("智能解析")
        self.parse_btn.setEnabled(True)
        self._ai_worker = None
        if not ok:
            self.warn_lab.setText("AI 解析失败，已使用规则结果")
            return
        rows = dedup_chunks(self._ai_rows)
        if not rows:
            self.warn_lab.setText("AI 没有产出有效结果，已使用规则结果")
            return
        # 用户在表格里改过的日期/时间/重复写回规则事件，作为合并基准
        self._sync_table_to_events()
        valid_sids = {s.sid for s in self._pipeline.sentences}
        merged, stats = match(
            self._pipeline.events, rows,
            self._pipeline.date_cands, self._pipeline.time_cands,
            self._pipeline.recur_cands, valid_sids,
            self._edited_by_row(), date.today(), self._n_chunks)
        self._apply_merged(merged, stats)

    def _sync_table_to_events(self):
        """把用户在表格里改过的日期/时间/重复/标题写回 P0 规则事件。"""
        for r in range(min(self._rule_count, self.table.rowCount())):
            ev = self.events[r]
            de = self.table.cellWidget(r, 1)
            te = self.table.cellWidget(r, 2)
            rc = self.table.cellWidget(r, 3)
            if de is not None:
                qd = de.date()
                ev.due_date = f"{qd.year():04d}-{qd.month():02d}-{qd.day():02d}"
                ev.ambiguous = False       # 用户明确了日期，不再算存疑
            if te is not None:
                ev.due_time = self._parse_time(te.text())
            if rc is not None:
                ev.recur = rc.currentData()
            item = self.table.item(r, 5)
            if item is not None and (r, "title") in self._user_edited:
                ev.title = item.text().strip() or ev.title

    def _apply_merged(self, merged: list, stats: dict):
        """P2 结果上表：配对行原地更新（变化高亮 2 秒），AI 独有按两档追加。"""
        self.table.blockSignals(True)      # AI 更新不算用户编辑
        try:
            self.events = merged
            n_rule = sum(1 for ev in merged if not getattr(ev, "ai_new", False))
            for r, ev in enumerate(merged):
                if r < n_rule:
                    self._update_row(r, ev)
                else:
                    self._append_row(r, ev)
            self._rule_count = n_rule
        finally:
            self.table.blockSignals(False)
        skipped = len({f for f in self._user_edited if f[0] < self._rule_count})
        msg = (f"AI 已优化 {stats['title_opt'] + stats['detail_opt']} 处标题/备注，"
               f"AI 新增 {stats['ai_new']} 件（自动勾选 {stats['ai_new_auto']} 件）")
        if skipped:
            msg += f"，跳过用户已改 {skipped} 处"
        self.warn_lab.setText(msg)

    def _flash(self, item, base):
        """变化单元格高亮 2 秒后恢复基础底色（base=None 表示默认）。"""
        item.setBackground(QColor(C.NEAR_BG))
        QTimer.singleShot(2000, lambda: item.setData(
            Qt.ItemDataRole.BackgroundRole, base))

    def _update_row(self, r: int, ev: ParsedEvent):
        """配对行原地更新：标题/备注/缺失日期补值，变化处高亮。"""
        item = self.table.item(r, 5)
        if item is not None:
            base = item.data(Qt.ItemDataRole.BackgroundRole)
            if item.text() != ev.title:
                item.setText(ev.title)
                self._flash(item, base)
            item.setToolTip(ev.detail or "")
        de = self.table.cellWidget(r, 1)
        if de is not None and ev.due_date:
            qd = de.date()
            iso = f"{qd.year():04d}-{qd.month():02d}-{qd.day():02d}"
            if iso != ev.due_date:
                d = date.fromisoformat(ev.due_date)
                de.setDate(QDate(d.year, d.month, d.day))
                de.setStyleSheet("border:1px solid #E8870C;")
                QTimer.singleShot(2000, lambda: de.setStyleSheet(""))

    def _append_row(self, r: int, ev: ParsedEvent):
        """AI 独有行追加：无日期时暂用绑定日期并标存疑。"""
        if not ev.due_date:
            ev.due_date = self.bind_edit.date().toString("yyyy-MM-dd")
            ev.ambiguous = True
            ev.warnings = ["AI 未给出日期，暂用绑定日期，请核对"]
        self._make_row(r, ev)

    def _fill_table(self):
        """P0 规则结果整表填充（毫秒级）。"""
        self.table.blockSignals(True)
        try:
            self.table.setRowCount(0)
            for r, ev in enumerate(self.events):
                self._make_row(r, ev)
        finally:
            self.table.blockSignals(False)
        n_warn = sum(1 for e in self.events if e.ambiguous)
        if self.events:
            self.warn_lab.setText(
                f"识别到 {len(self.events)} 件事" +
                (f"，其中 {n_warn} 件存疑，请核对" if n_warn else ""))
        else:
            self.warn_lab.setText("没有识别到内容")

    def _make_row(self, r: int, ev: ParsedEvent):
        """构造一行：导入/日期/时间/重复/类型/事项，并挂用户编辑跟踪。"""
        self.table.insertRow(r)

        chk = QCheckBox()
        chk.setChecked(bool(getattr(ev, "auto_checked", True)))
        holder = QWidget()
        h = QHBoxLayout(holder)
        h.addWidget(chk)
        h.setAlignment(Qt.AlignmentFlag.AlignCenter)
        h.setContentsMargins(0, 0, 0, 0)
        self.table.setCellWidget(r, 0, holder)

        de = NoWheelDateEdit()
        de.setCalendarPopup(True)
        de.setDisplayFormat("yyyy-MM-dd")
        if ev.due_date:
            d = date.fromisoformat(ev.due_date)
            de.setDate(QDate(d.year, d.month, d.day))
        else:
            de.setDate(self.bind_edit.date())
        self.table.setCellWidget(r, 1, de)

        te = QLineEdit(ev.due_time or "")
        te.setPlaceholderText("无 / HH:MM")
        te.setMaxLength(5)
        self.table.setCellWidget(r, 2, te)

        rc = QComboBox()
        for k in RECUR_KEYS:
            rc.addItem(RECUR_LABEL[k], k)
        rc.setCurrentIndex(RECUR_KEYS.index(
            ev.recur if ev.recur in RECUR_KEYS else RECUR_NONE))
        self.table.setCellWidget(r, 3, rc)

        tc = QComboBox()
        tc.addItem("短期", "short")
        tc.addItem("长期", "long")
        tc.setCurrentIndex(0 if getattr(ev, "task_type", "short") == "short" else 1)
        self.table.setCellWidget(r, 4, tc)

        title_item = QTableWidgetItem(ev.title)
        title_item.setToolTip(ev.detail or "")
        base = None
        ai_new = getattr(ev, "ai_new", False)
        if ai_new:
            auto = getattr(ev, "auto_checked", False)
            base = QColor(C.NEAR_BG if auto else C.URGENT_BG)
            title_item.setBackground(base)
            tip = "AI 新增，请复核" if auto else "AI 新增，需人工确认"
            if ev.detail:
                tip += "；" + ev.detail
            title_item.setToolTip(tip)
        elif ev.ambiguous and not self.chk_bind.isChecked():
            # 强制绑定日期后，日期已由用户明确指定，不再标存疑
            base = QColor("#FFF4DC")
            title_item.setBackground(base)
            title_item.setToolTip("；".join(ev.warnings) or ev.detail)
        self.table.setItem(r, 5, title_item)
        self.table.setRowHeight(r, 34)

        # 用户编辑跟踪（AI 合并时跳过这些字段）
        de.dateChanged.connect(lambda *_: self._user_edited.add((r, "date")))
        te.textChanged.connect(lambda *_: self._user_edited.add((r, "time")))
        rc.currentIndexChanged.connect(lambda *_: self._user_edited.add((r, "recur")))

    def _apply_bind(self, *_):
        forced = self.chk_bind.isChecked()
        self.bind_edit.setEnabled(forced)
        qd = self.bind_edit.date()
        for r in range(self.table.rowCount()):
            de: QDateEdit = self.table.cellWidget(r, 1)
            if de is None:
                continue
            if forced:
                de.setDate(qd)
            else:
                ev = self.events[r] if r < len(self.events) else None
                if ev and ev.due_date:
                    d = date.fromisoformat(ev.due_date)
                    de.setDate(QDate(d.year, d.month, d.day))
            de.setDisabled(forced)
        self._update_bind_warning()

    _BIND_HINT_NORMAL = ("勾选后所有事项统一落在这一天（默认取文本里识别到的最近日期，"
                         "文本没日期时才取日历选中日；滚轮不会误改，可点“今天”重置）")

    def _update_bind_warning(self):
        """绑定日期与文本识别日期差太多时（如翻日历停在了2027年），醒目提醒。"""
        if not (self.chk_bind.isChecked() and self.events):
            self.bind_hint.setText(self._BIND_HINT_NORMAL)
            self.bind_hint.setStyleSheet(f"color:{C.TEXT_SUB};")
            return
        bd = self.bind_edit.date()
        parsed = []
        for ev in self.events:
            if ev.ambiguous or not ev.due_date:
                continue
            try:
                y, m, d = map(int, ev.due_date.split("-"))
                parsed.append(QDate(y, m, d))
            except ValueError:
                pass
        if parsed and all(abs(bd.daysTo(d)) > 31 for d in parsed):
            dates_txt = "、".join(sorted({d.toString("yyyy-MM-dd") for d in parsed}))
            self.bind_hint.setText(
                f"⚠ 文本里识别到的日期是 {dates_txt}，与绑定日期 "
                f"{bd.toString('yyyy-MM-dd')} 相差超过1个月，确认要全部落这一天吗？"
                "不对就改日期或取消勾选")
            self.bind_hint.setStyleSheet("color:#E8870C; font-weight:600;")
        else:
            self.bind_hint.setText(self._BIND_HINT_NORMAL)
            self.bind_hint.setStyleSheet(f"color:{C.TEXT_SUB};")

    def accepted_events(self) -> list[dict]:
        out = []
        for r in range(self.table.rowCount()):
            holder = self.table.cellWidget(r, 0)
            chk = holder.findChild(QCheckBox) if holder else None
            if chk is None or not chk.isChecked():
                continue
            de: QDateEdit = self.table.cellWidget(r, 1)
            te: QLineEdit = self.table.cellWidget(r, 2)
            rc: QComboBox = self.table.cellWidget(r, 3)
            tc: QComboBox = self.table.cellWidget(r, 4)
            qd = de.date()
            out.append({
                "title": self.table.item(r, 5).text().strip() or "未命名事项",
                "due_date": f"{qd.year():04d}-{qd.month():02d}-{qd.day():02d}",
                "due_time": self._parse_time(te.text()),
                "detail": self.events[r].detail if r < len(self.events) else "",
                "recur": rc.currentData(),
                "task_type": tc.currentData() if tc else "short",
                "lead_days": "1,3",
            })
        return out
