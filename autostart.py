# -*- coding: utf-8 -*-
"""Windows 快捷方式管理：开机自启（启动文件夹）+ 桌面图标。

不走注册表、不需要管理员权限；目标用 pythonw.exe（无黑控制台窗口）。
"""
import os
import subprocess
import sys

APP_NAME = "日历提醒"

if getattr(sys, "frozen", False):
    # 打包态：快捷方式直接指向 exe 自身，无参数
    WORK_DIR = os.path.dirname(os.path.abspath(sys.executable))
    _TARGET = sys.executable
    _ARGS = ""
else:
    WORK_DIR = os.path.dirname(os.path.abspath(__file__))
    _TARGET = None  # 运行时由 _pythonw_path() 解析
    _ARGS = os.path.join(WORK_DIR, "main.py")

STARTUP_LNK = os.path.join(
    os.environ.get("APPDATA", ""),
    r"Microsoft\Windows\Start Menu\Programs\Startup",
    f"{APP_NAME}.lnk",
)
DESKTOP_LNK = os.path.join(
    os.environ.get("USERPROFILE", ""), "Desktop", f"{APP_NAME}.lnk"
)


def _pythonw_path() -> str:
    """同目录下的 pythonw.exe（无控制台），找不到就退回 python.exe。"""
    d = os.path.dirname(sys.executable)
    cand = os.path.join(d, "pythonw.exe")
    return cand if os.path.exists(cand) else sys.executable


def _ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def create_shortcut(lnk_path: str) -> bool:
    target = _TARGET if _TARGET is not None else _pythonw_path()
    ps = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut({_ps_quote(lnk_path)}); "
        f"$s.TargetPath = {_ps_quote(target)}; "
        f"$s.Arguments = {_ps_quote(_ARGS)}; "
        f"$s.WorkingDirectory = {_ps_quote(WORK_DIR)}; "
        "$s.IconLocation = " + _ps_quote(
            (target if getattr(sys, "frozen", False)
             else os.path.join(WORK_DIR, "app.ico"))) + "; "
        f"$s.Description = {_ps_quote('本地日历与待办提醒')}; "
        "$s.Save()"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=20,
        )
        return r.returncode == 0 and os.path.exists(lnk_path)
    except Exception:
        return False


def remove_shortcut(lnk_path: str) -> bool:
    try:
        if os.path.exists(lnk_path):
            os.remove(lnk_path)
        return True
    except Exception:
        return False


def is_autostart_enabled() -> bool:
    return os.path.exists(STARTUP_LNK)


def set_autostart(enabled: bool) -> bool:
    if enabled:
        return create_shortcut(STARTUP_LNK)
    return remove_shortcut(STARTUP_LNK)


def ensure_desktop_icon() -> bool:
    if os.path.exists(DESKTOP_LNK):
        return True
    return create_shortcut(DESKTOP_LNK)
