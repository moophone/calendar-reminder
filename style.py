# -*- coding: utf-8 -*-
"""全局主题系统（设计文档 §9）：5 套色板 dict + QSS 模板函数 + apply_theme 一键换肤。

- THEMES：5 套 × 20 键，色值以画布 colors_and_type.css 的 --app-* 令牌为准（§9.2 全表）；
  标 † 的派生值（cool/forest/dark/anime 的 ACCENT_DARK、forest 的 CARD）按 §9.2 脚注落库。
- C：运行时活动色板（_Palette，dict 子类，支持 C.ACCENT 属性式取色）。
  自绘代码（MiniCalendar 格子、FloatBall、make_app_icon、卡片点阵等）一律从 C 取色；
  apply_theme 原地更新 C 后，各组件重绘即生效（§9.3）。
- build_qss(name)：由色板生成全局 QSS；模块级 QSS = build_qss("warm") 保留一个版本周期。
- apply_theme(app, name)：setStyleSheet + 持久化 theme + 原地刷新 C。
"""
import storage

# ==================== §9.2 主题常量总表（5 套 × 20 键） ====================

THEMES = {
    "warm": {   # 暖纸（=画布值）
        "ACCENT": "#C45D3A", "ACCENT_DARK": "#B05232",
        "DANGER": "#D64545", "WARN": "#C4832B",
        "BG": "#FDF8F3", "CARD": "#F7F1EA",
        "TEXT": "#2C2420", "TEXT_SUB": "#6B5F58", "BORDER": "#DED6CE",
        "LONG_BG": "#F3EDD8", "LONG_FG": "#A68A4C",
        "NEAR_BG": "#F9EDD8", "NEAR_FG": "#C4832B",
        "URGENT_BG": "#FBE8E8", "URGENT_FG": "#D64545",
        "TODAY_BG": "#F7E8E3", "SEL_BG": "#F7E8E3", "HOVER_BG": "#EFE9E1",
        "DONE_DOT": "#9A8F88", "RECUR_DOT": "#4A8A5E",
    },
    "cool": {   # 冷灰
        "ACCENT": "#2563EB", "ACCENT_DARK": "#1D4ED8",
        "DANGER": "#DC2626", "WARN": "#D97706",
        "BG": "#F7F8FA", "CARD": "#FFFFFF",
        "TEXT": "#1F2328", "TEXT_SUB": "#59636E", "BORDER": "#D1D5DA",
        "LONG_BG": "#E0E7FF", "LONG_FG": "#4F46E5",
        "NEAR_BG": "#FEF3C7", "NEAR_FG": "#D97706",
        "URGENT_BG": "#FEE2E2", "URGENT_FG": "#DC2626",
        "TODAY_BG": "#DBEAFE", "SEL_BG": "#DBEAFE", "HOVER_BG": "#F0F1F4",
        "DONE_DOT": "#8C959F", "RECUR_DOT": "#16A34A",
    },
    "forest": {   # 森林
        "ACCENT": "#2D6A4F", "ACCENT_DARK": "#1B4332",
        "DANGER": "#B23A48", "WARN": "#B69121",
        "BG": "#F4F7F4", "CARD": "#FCFEFC",
        "TEXT": "#1A2E1A", "TEXT_SUB": "#4A5D4A", "BORDER": "#BFD1C0",
        "LONG_BG": "#D8F3DC", "LONG_FG": "#52796F",
        "NEAR_BG": "#F3EAC2", "NEAR_FG": "#B69121",
        "URGENT_BG": "#FADADD", "URGENT_FG": "#B23A48",
        "TODAY_BG": "#D8F3DC", "SEL_BG": "#D8F3DC", "HOVER_BG": "#E3EBE3",
        "DONE_DOT": "#7A8F7A", "RECUR_DOT": "#2D6A4F",
    },
    "dark": {   # 夜间
        "ACCENT": "#E07B39", "ACCENT_DARK": "#B85E28",
        "DANGER": "#E56464", "WARN": "#E5A53E",
        "BG": "#1A1D21", "CARD": "#242830",
        "TEXT": "#E8ECEF", "TEXT_SUB": "#A8AFB6", "BORDER": "#3A414B",
        "LONG_BG": "#3A3224", "LONG_FG": "#C9A86A",
        "NEAR_BG": "#3A3224", "NEAR_FG": "#E5A53E",
        "URGENT_BG": "#3D2426", "URGENT_FG": "#E56464",
        "TODAY_BG": "#3D2E22", "SEL_BG": "#3D2E22", "HOVER_BG": "#2E333C",
        "DONE_DOT": "#6E7780", "RECUR_DOT": "#5FB982",
    },
    "anime": {   # 动漫蓝
        "ACCENT": "#2B5585", "ACCENT_DARK": "#1F4068",
        "DANGER": "#B94C4C", "WARN": "#C17A2A",
        "BG": "#F8F1EF", "CARD": "#FFFFFF",
        "TEXT": "#1F2937", "TEXT_SUB": "#5D6470", "BORDER": "#E8DCDA",
        "LONG_BG": "#F3EDF4", "LONG_FG": "#8B6D8C",
        "NEAR_BG": "#FDF2E0", "NEAR_FG": "#C17A2A",
        "URGENT_BG": "#F9E5E5", "URGENT_FG": "#B94C4C",
        "TODAY_BG": "#E8F0F8", "SEL_BG": "#E8F0F8", "HOVER_BG": "#F5EAEB",
        "DONE_DOT": "#9CA3AF", "RECUR_DOT": "#3D7A5A",
    },
}

THEME_NAMES = {
    "warm": "暖纸", "cool": "冷灰", "forest": "森林",
    "dark": "夜间", "anime": "动漫蓝",
}

# §9.6 各主题 shadow-color（shadow-1/2/3 的 rgb 部分；dark 不透明度另调）
SHADOW_RGB = {
    "warm": (44, 36, 32), "cool": (31, 35, 40), "forest": (26, 46, 26),
    "dark": (0, 0, 0), "anime": (31, 41, 55),
}


class _Palette(dict):
    """运行时活动色板：dict 存储 + 属性式取色（C.ACCENT ≡ C['ACCENT']）。"""

    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k)


C = _Palette(THEMES["warm"])
C["SHADOW_RGB"] = SHADOW_RGB["warm"]


# ==================== §9.3 QSS 模板函数 ====================

def build_qss(name: str = "warm") -> str:
    c = THEMES.get(name, THEMES["warm"])
    return f"""
* {{
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif;
    font-size: 13px;
    color: {c['TEXT']};
    outline: none;
}}

QWidget#rootBg {{
    background: {c['BG']};
}}

QWidget.card {{
    background: {c['CARD']};
    border-radius: 14px;
    border: 1px solid {c['BORDER']};
}}

QLabel#appTitle {{
    font-size: 16px;
    font-weight: 600;
    color: {c['TEXT']};
    letter-spacing: 1px;
}}
QLabel.subtle {{
    color: {c['TEXT_SUB']};
    font-size: 12px;
}}

QPushButton {{
    background: {c['CARD']};
    border: 1px solid {c['BORDER']};
    border-radius: 9px;
    padding: 7px 14px;
    font-weight: 600;
}}
QPushButton:hover {{
    border-color: {c['ACCENT']};
    color: {c['ACCENT']};
}}
QPushButton:pressed {{
    background: {c['HOVER_BG']};
}}

QPushButton#primary {{
    background: {c['ACCENT']};
    color: white;
    border: none;
    padding: 9px 18px;
}}
QPushButton#primary:hover {{
    background: {c['ACCENT_DARK']};
    color: white;
}}
QPushButton#dangerText {{
    background: transparent;
    border: none;
    color: {c['DANGER']};
    padding: 4px 8px;
}}
QPushButton#dangerText:hover {{
    background: {c['URGENT_BG']};
    border-radius: 6px;
}}

/* ---------- 任务卡片（中栏列表行） ---------- */
QWidget#taskCard {{ background: {c['CARD']}; border: 1px solid {c['BORDER']}; border-radius: 12px; }}
QWidget#taskCard[selected="true"] {{ background: {c['TODAY_BG']}; border: 1px solid {c['ACCENT']}; }}
QWidget#taskCard[hl="true"] {{ background: {c['TODAY_BG']}; }}

/* ---------- 日期选择弹层（QDateEdit calendarPopup 内部仍是 QCalendarWidget） ---------- */
QCalendarWidget QWidget {{
    alternate-background-color: {c['CARD']};
    background: {c['CARD']};
}}
QCalendarWidget QToolButton {{
    background: transparent;
    border: none;
    font-size: 15px;
    font-weight: 700;
    padding: 6px 14px;
    border-radius: 8px;
}}
QCalendarWidget QToolButton:hover {{
    background: {c['HOVER_BG']};
    color: {c['ACCENT']};
}}
QCalendarWidget QMenu {{
    background: {c['CARD']};
    border: 1px solid {c['BORDER']};
    border-radius: 8px;
}}
QCalendarWidget QSpinBox {{
    background: {c['CARD']};
    border: 1px solid {c['BORDER']};
    border-radius: 6px;
    padding: 2px 6px;
}}
QCalendarWidget QAbstractItemView:enabled {{
    background: {c['CARD']};
    color: {c['TEXT']};
    selection-background-color: {c['ACCENT']};
    selection-color: white;
    border: none;
    border-radius: 8px;
}}
#calCard {{
    background: {c['CARD']};
    border-radius: 14px;
    border: 1px solid {c['BORDER']};
}}

/* ---------- 列表 / 输入 ---------- */
QListWidget, QTextEdit, QLineEdit, QTableWidget, QDateEdit, QTimeEdit, QComboBox {{
    background: {c['CARD']};
    border: 1px solid {c['BORDER']};
    border-radius: 10px;
    padding: 6px 8px;
    selection-background-color: {c['SEL_BG']};
    selection-color: {c['ACCENT_DARK']};
}}
QListWidget {{
    border: none;
    background: transparent;
    padding: 0px;
}}
QListWidget::item {{
    border-radius: 10px;
    margin: 4px 0px;
}}
QListWidget::item:selected {{
    background: {c['SEL_BG']};
}}
QLineEdit:focus, QTextEdit:focus, QDateEdit:focus, QTimeEdit:focus, QComboBox:focus {{
    border: 1px solid {c['ACCENT']};
}}

QComboBox QAbstractItemView {{
    background: {c['CARD']};
    border: 1px solid {c['BORDER']};
    border-radius: 8px;
    selection-background-color: {c['SEL_BG']};
}}

QCheckBox {{
    spacing: 6px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 5px;
    border: 1.5px solid {c['BORDER']};
    background: {c['CARD']};
}}
QCheckBox::indicator:checked {{
    background: {c['ACCENT']};
    border-color: {c['ACCENT']};
}}

QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 4px;
}}
QScrollBar::handle:vertical {{
    background: {c['BORDER']};
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {c['DONE_DOT']};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 8px;
}}
QScrollBar::handle:horizontal {{
    background: {c['BORDER']};
    border-radius: 4px;
}}

QMenu {{
    background: {c['CARD']};
    border: 1px solid {c['BORDER']};
    border-radius: 10px;
    padding: 6px;
}}
QMenu::item {{
    padding: 7px 22px 7px 14px;
    border-radius: 7px;
}}
QMenu::item:selected {{
    background: {c['SEL_BG']};
    color: {c['ACCENT_DARK']};
}}
QMenu::item:disabled {{
    color: {c['TEXT_SUB']};
}}

QToolTip {{
    background: {c['CARD']};
    color: {c['TEXT']};
    border: 1px solid {c['BORDER']};
    padding: 4px 8px;
}}

QDialog {{
    background: {c['BG']};
}}
"""


# ==================== §9.3 换肤入口 ====================

def current_theme() -> str:
    return storage.get_setting("theme") or "warm"


def apply_theme(app, name: str):
    """三件事之一：setStyleSheet + 持久化 + 原地刷新 C（自绘组件随后重绘即生效）。"""
    if name not in THEMES:
        name = "warm"
    C.clear()
    C.update(THEMES[name])
    C["SHADOW_RGB"] = SHADOW_RGB[name]
    app.setStyleSheet(build_qss(name))
    storage.set_setting("theme", name)


# ==================== 兼容层（§9.3：保留一个版本周期） ====================

_w = THEMES["warm"]
ACCENT = _w["ACCENT"]
ACCENT_DARK = _w["ACCENT_DARK"]
DANGER = _w["DANGER"]
WARN = _w["WARN"]
BG = _w["BG"]
CARD = _w["CARD"]
TEXT = _w["TEXT"]
TEXT_SUB = _w["TEXT_SUB"]
BORDER = _w["BORDER"]
LONG_BG = _w["LONG_BG"]
LONG_FG = _w["LONG_FG"]
NEAR_BG = _w["NEAR_BG"]
NEAR_FG = _w["NEAR_FG"]
URGENT_BG = _w["URGENT_BG"]
URGENT_FG = _w["URGENT_FG"]
TODAY_BG = _w["TODAY_BG"]
SEL_BG = _w["SEL_BG"]
HOVER_BG = _w["HOVER_BG"]
DONE_DOT = _w["DONE_DOT"]
RECUR_DOT = _w["RECUR_DOT"]

QSS = build_qss("warm")
