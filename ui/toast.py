# -*- coding: utf-8 -*- 
"""Toast：非阻塞短提示（§8.9）。2.5s 自动消失，多条排队；纯 QLabel+动画。"""
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve
from PyQt6.QtWidgets import QLabel, QGraphicsOpacityEffect, QWidget

from style import C


class ToastManager:
    """挂在某个父窗口上，`show(text)` 即弹一条；一次只显示一条，其余排队。"""

    DURATION_MS = 2500

    def __init__(self, parent: QWidget):
        self._parent = parent
        self._queue: list[str] = []
        self._current: QLabel | None = None

    def show(self, text: str):
        if not text:
            return
        self._queue.append(text)
        self._pump()

    def _pump(self):
        if self._current is not None or not self._queue:
            return
        text = self._queue.pop(0)
        lab = QLabel(text, self._parent)
        lab.setStyleSheet(
            f"background:{C.CARD};color:{C.TEXT};border:1px solid {C.BORDER};"
            "border-radius:8px;padding:8px 16px;font-size:13px;")
        lab.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lab.setWindowFlags(Qt.WindowType.SubWindow)
        lab.adjustSize()
        self._reposition(lab)
        lab.show()
        lab.raise_()

        eff = QGraphicsOpacityEffect(lab)
        lab.setGraphicsEffect(eff)
        fade_in = QPropertyAnimation(eff, b"opacity", lab)
        fade_in.setDuration(150)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(1.0)
        fade_in.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade_in.start()

        self._current = lab
        self._fade_in = fade_in  # 防 GC
        QTimer.singleShot(self.DURATION_MS, lambda: self._dismiss(lab, eff))

    def _dismiss(self, lab: QLabel, eff: QGraphicsOpacityEffect):
        fade_out = QPropertyAnimation(eff, b"opacity", lab)
        fade_out.setDuration(300)
        fade_out.setStartValue(1.0)
        fade_out.setEndValue(0.0)
        fade_out.finished.connect(lambda: self._cleanup(lab))
        fade_out.start()
        self._fade_out = fade_out

    def _cleanup(self, lab: QLabel):
        lab.hide()
        lab.deleteLater()
        if self._current is lab:
            self._current = None
        self._pump()

    def _reposition(self, lab: QLabel):
        p = self._parent
        lab.move((p.width() - lab.width()) // 2,
                 p.height() - lab.height() - 28)

    def on_parent_resize(self):
        if self._current is not None:
            self._reposition(self._current)
