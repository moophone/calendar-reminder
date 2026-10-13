# -*- coding: utf-8 -*-
"""日历提醒 —— 入口。

运行：pythonw main.py（或桌面/启动文件夹快捷方式）
组成：主窗口（月历+历程） + 桌面悬浮球 + 托盘 + 后台提醒引擎。
关主窗口只缩起，程序靠悬浮球/托盘常驻；真正退出走托盘或小球右键。
"""
import os
import sys
from datetime import date

# 允许从任意工作目录启动（快捷方式场景）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QAction
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import QApplication, QSystemTrayIcon, QMenu

import storage
import autostart
import llm_fallback
import style
from ui import MainWindow, FloatBall, ReminderPopup, make_app_icon
from reminders import ReminderEngine, today_brief

SINGLE_NAME = "rili_calendar_v2_singleton"


def _raise_running_instance() -> bool:
    """若已有实例在运行，通知它唤起窗口并返回 True（本实例应退出）。"""
    sock = QLocalSocket()
    sock.connectToServer(SINGLE_NAME)
    ok = sock.waitForConnected(300)
    if ok:
        sock.write(b"raise")
        sock.flush()
        sock.waitForBytesWritten(300)
        sock.disconnectFromServer()
        return True
    return False


class CalendarApp:
    def __init__(self, app: QApplication):
        self.app = app
        self.popup = None

        # 单实例守卫：第二个实例双击 exe 时，唤起本窗口而非重复开一份
        # （QLocalServer 不是 QObject，不能传 CalendarApp 当 parent）
        self._single = QLocalServer()
        self._single.removeServer(SINGLE_NAME)  # 清上次异常退出的残留
        self._single.listen(SINGLE_NAME)
        self._single.newConnection.connect(self._on_second_instance)

        self.window = MainWindow()
        self.ball = FloatBall()
        self.engine = ReminderEngine()

        self._build_tray()
        self._wire()
        self._first_run()

        self.ball.show()
        self.refresh_counts()

        # 本地模型后台预加载（可用时），用户点智能解析免等首次启动
        llm_fallback.preload()

        # 开机后先让界面出来，再做首轮提醒检查（不阻塞交互）
        QTimer.singleShot(900, lambda: (
            self.engine.check(force=True),
            self.engine.check_long_daily(force=True)))

    # ---------- 托盘 ----------
    def _build_tray(self):
        self.tray = QSystemTrayIcon(make_app_icon(), self.app)
        self.tray.setToolTip("日历提醒")
        menu = QMenu()
        self.a_open = menu.addAction("打开主界面")
        self.a_open.triggered.connect(self.show_window)

        self.a_ball = menu.addAction("显示悬浮球")
        self.a_ball.triggered.connect(self.ball.show)

        self.a_auto = QAction("开机自启", menu, checkable=True)
        self.a_auto.setChecked(autostart.is_autostart_enabled())
        self.a_auto.toggled.connect(self._toggle_autostart)
        menu.addAction(self.a_auto)

        menu.addSeparator()
        a_quit = menu.addAction("退出")
        a_quit.triggered.connect(self.quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    # ---------- 接线 ----------
    def _wire(self):
        # 悬浮球
        self.ball.clicked_ball.connect(self.toggle_window)
        self.ball.quit_requested.connect(self.quit)
        self.ball.hide_requested.connect(self.ball.hide)
        self.ball.check_requested.connect(lambda: (
            self.engine.check(force=True),
            self.engine.check_long_daily(force=True)))

        # 数据变化（导入/完成/写历程）→ 刷新角标
        self.window.data_changed.connect(self.refresh_counts)
        # §9.3 换肤三件事之二三：托盘图标重建 + 悬浮球重绘
        self.window.theme_changed.connect(self._on_theme_changed)
        self.engine.ticked.connect(self._on_tick)
        self.engine.items_ready.connect(self.show_reminder_popup)
        self._last_day = date.today()

    def _on_tick(self):
        self.refresh_counts()
        # 跨天：日历与任务列表按新日期重排（日视图浏览历史日期不被打断）
        if date.today() != self._last_day:
            self._last_day = date.today()
            self.window.go_today()

    # ---------- 提醒弹窗 ----------
    def show_reminder_popup(self, items):
        # 已有弹窗打开就不叠加；稍后/关闭后下一轮检查自然补上
        if self.popup is not None:
            return
        self.popup = ReminderPopup(items)
        self.popup.snoozed.connect(self.on_snooze)
        self.popup.task_done.connect(self.on_popup_done)
        self.popup.dismissed.connect(self._popup_closed)
        self.popup.show()
        self.popup.raise_()
        self.popup.activateWindow()
        self.app.beep()
        QTimer.singleShot(900, self.app.beep)
        self.tray.showMessage(
            "日历提醒",
            "\n".join(f"· {it.task['title']}" for it in items[:5])
            + ("" if len(items) <= 5 else f"\n…共 {len(items)} 件"),
            make_app_icon(),
            4000,
        )

    def _popup_closed(self):
        self.popup = None

    def on_snooze(self, items):
        keys = [it.log_key for it in items]
        storage.clear_reminded(keys)
        self.engine.snooze(items, minutes=10)
        # 单条稍时时弹窗仍在：保持单例引用；关闭由 dismissed 统一清零
        if self.popup is not None and not self.popup.isVisible():
            self.popup = None

    def on_popup_done(self, task_id):
        storage.complete_task(task_id)
        self.window.refresh_all()
        self.refresh_counts()

    # ---------- 窗口/角标 ----------
    def _on_second_instance(self):
        """第二个实例试图启动：接管连接并把主窗口唤到最前。"""
        c = self._single.nextPendingConnection()
        if c is not None:
            c.waitForReadyRead(200)
            c.deleteLater()
        self.show_window()

    def show_window(self):
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()
        self.window.refresh_panel()

    def toggle_window(self):
        if self.window.isVisible():
            self.window.hide()
        else:
            self.show_window()

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_window()

    def refresh_counts(self):
        n_today, n_overdue = today_brief()
        self.ball.set_counts(n_today, n_overdue)

    def _toggle_autostart(self, on: bool):
        ok = autostart.set_autostart(on)
        self.a_auto.setChecked(autostart.is_autostart_enabled())

    def _on_theme_changed(self, _name: str):
        self.tray.setIcon(make_app_icon())
        self.ball.update()

    # ---------- 首次运行 ----------
    def _first_run(self):
        if storage.get_setting("installed") != "1":
            autostart.ensure_desktop_icon()
            autostart.set_autostart(True)
            self.a_auto.setChecked(True)
            storage.set_setting("installed", "1")

    def quit(self):
        self.tray.hide()
        self.ball.hide()
        self.app.quit()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("日历提醒")
    app.setQuitOnLastWindowClosed(False)
    # 已有实例在跑 → 唤起它，本进程立即退出（不重复开第二份）
    if _raise_running_instance():
        sys.exit(0)
    style.apply_theme(app, style.current_theme())
    instance = CalendarApp(app)
    # 启动时主动显示一次主界面，方便第一次使用
    QTimer.singleShot(300, instance.show_window)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
