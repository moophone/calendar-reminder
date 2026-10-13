# -*- coding: utf-8 -*-
"""中文自由文本 → 事项列表（纯离线规则解析，不依赖任何网络/API）。

一行（或一个分句）= 一件事。支持：
  2026-10-10 / 2026年10月10日 / 10月10号 / 10月10日下午3点半
  今天、明天、后天、大后天、3天后
  周五 / 下周五 / 下下周五 / 礼拜三 / 星期三
  11月底 / 11月（无具体日期，默认1号，返回 ambiguous=True 提示确认）
  每天、每周三、每月15号、每年10月24日
  提前3天（设置预警天数）
"""
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

WEEKDAY_MAP = {"一": 0, "二": 1, "三": 2, "四": 3,
               "五": 4, "六": 5, "日": 6, "天": 6}


@dataclass
class ParsedEvent:
    title: str
    due_date: str | None        # YYYY-MM-DD
    due_time: str | None = None  # HH:MM
    recur: str = "none"          # none/daily/weekly/monthly/yearly
    lead_days: str = "1,3"
    ambiguous: bool = False      # 解析存疑（如只有月份），导入时需用户确认
    raw: str = ""
    warnings: list = field(default_factory=list)
    detail: str = ""             # 整段合并模式下，原文存到任务备注
    has_cue: bool = False        # 这一句里是否出现了日期/时间/重复等时间线索
    source_sentences: list = field(default_factory=list)  # P0：来源句子编号 ["S1",...]


# ---------------- P0 管线数据结构 ----------------

@dataclass
class Sentence:
    sid: str      # "S1"
    text: str


@dataclass
class Candidate:
    cid: str      # "D1" / "T1" / "R1"
    raw: str      # 原文片段
    value: str    # ISO 日期 / HH:MM / recur key
    ambiguous: bool = False


@dataclass
class PipelineResult:
    sentences: list[Sentence]
    date_cands: list[Candidate]
    time_cands: list[Candidate]
    recur_cands: list[Candidate]
    events: list[ParsedEvent]


# ---------------- 分句 ----------------

def split_events(text: str) -> list[str]:
    """把粘贴的整段文字切成一条条事项。"""
    # 先按换行分；一行内再按句末标点分（逗号不切，一句话里常有逗号）
    chunks = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = re.split(r"[。；;！!？?\n]+", line)
        for p in parts:
            p = p.strip(" \t-·•*、,，")
            if p:
                chunks.append(p)
    return chunks


# ---------------- 时间提取 ----------------

_PERIOD_AM = ("上午", "早上", "早晨", "清晨", "凌晨")
_PERIOD_PM = ("下午", "傍晚", "晚上", "晚")


def _extract_time(text: str):
    """返回 (HH:MM 或 None, 清理掉时间表达后的文本)。"""
    hhmm = None
    # 上午10:30 / 下午3点半 / 晚上8点 / 10:00
    # 分隔符（: ： 点）必须存在，裸数字不会被误判为时间；分钟可省略
    pat = re.compile(
        r"(上午|早上|早晨|清晨|凌晨|中午|下午|傍晚|晚上|晚)?"
        r"\s*(\d{1,2})\s*[:：点]\s*(?:(\d{1,2}|半)\s*分?)?"
    )
    matches = list(pat.finditer(text))
    for m in matches:
        period, h_s, m_s = m.group(1), m.group(2), m.group(3)
        h = int(h_s)
        minute = 0
        if m_s == "半":
            minute = 30
        elif m_s:
            minute = int(m_s)
        # 排除明显不是时间的情况（如 "2026年" 的片段不会进到这里，因为前面有年/月/日）
        if h > 24 or minute > 59:
            continue
        if period in _PERIOD_PM and h < 12:
            h += 12
        elif period == "中午" and h < 11:
            h += 12
        elif period in _PERIOD_AM and h == 12:
            h = 0
        hhmm = f"{h:02d}:{minute:02d}"
        text = text[:m.start()] + " " + text[m.end():]
        break  # 一件事只取第一个时间
    return hhmm, re.sub(r"\s+", " ", text).strip()


def _extract_date(text: str, today: date):
    """返回 (date 或 None, ambiguous:bool, 清理后文本, 警告)。"""
    ambiguous = False
    warning = None

    # 1) 完整年份 2026-10-10 / 2026/10/10 / 2026年10月10日
    m = re.search(r"(\d{4})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})\s*[号日]?", text)
    if m:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return d, False, text[:m.start()] + " " + text[m.end():], None

    # 2) X月底
    m = re.search(r"(\d{1,2})\s*月\s*(底)", text)
    if m:
        month = int(m.group(1))
        year = _guess_year(month, 28, today)
        d = date(year, month, _days_in_month(year, month))
        return d, False, text[:m.start()] + " " + text[m.end():], None

    # 3) X月X号
    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[号日]", text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        year = _guess_year(month, day, today)
        d = date(year, month, day)
        return d, False, text[:m.start()] + " " + text[m.end():], None

    # 4) N天后 / N天以后
    m = re.search(r"(\d{1,3})\s*天(?:以后|之后|过后|后)", text)
    if m:
        d = today + timedelta(days=int(m.group(1)))
        return d, False, text[:m.start()] + " " + text[m.end():], None

    # 5) 今天 / 明天 / 后天 / 大后天
    relative_words = [("大后天", 3), ("后天", 2), ("明天", 1),
                      ("明日", 1), ("今天", 0), ("今日", 0), ("今晚", 0)]
    for word, delta in relative_words:
        if word in text:
            d = today + timedelta(days=delta)
            return d, False, text.replace(word, " ", 1), None

    # 6) 周X（可带 本/这/下/下下）
    m = re.search(
        r"(下下|下个|下|本|这个|这)?\s*(?:周|星期|礼拜)\s*([一二三四五六日天])",
        text,
    )
    if m:
        prefix, wd_s = m.group(1), m.group(2)
        target_wd = WEEKDAY_MAP[wd_s]
        base = today - timedelta(days=today.weekday())  # 本周一
        if prefix in ("下下", "下个下"):
            base += timedelta(weeks=2)
        elif prefix in ("下", "下个"):
            base += timedelta(weeks=1)
        d = base + timedelta(days=target_wd)
        if prefix in (None, "本", "这", "这个") and d < today:
            d += timedelta(weeks=1)
        return d, False, text[:m.start()] + " " + text[m.end():], None

    # 7) 只有 X月（没有日）→ 默认1号，标记存疑
    m = re.search(r"(\d{1,2})\s*月(?!\s*\d)", text)
    if m:
        month = int(m.group(1))
        if 1 <= month <= 12:
            year = _guess_year(month, 1, today)
            d = date(year, month, 1)
            ambiguous = True
            warning = "只写了月份、没写具体日期，已暂定为1号，请在确认表里改"
            return d, True, text[:m.start()] + " " + text[m.end():], warning

    return None, ambiguous, text, warning


def _guess_year(month: int, day: int, today: date) -> int:
    """没写年份时：该日期今年还没到就用今年，已过则默认明年。"""
    candidate = date(today.year, month, day)
    if candidate < today:
        return today.year + 1
    return today.year


def _days_in_month(y: int, m: int) -> int:
    if m == 12:
        nxt = date(y + 1, 1, 1)
    else:
        nxt = date(y, m + 1, 1)
    return (nxt - timedelta(days=1)).day


# ---------------- 重复规则 ----------------

def _extract_recur(text: str, today: date):
    """返回 (recur, due_date 或 None, 清理后文本)。"""
    # 每年10月24日
    m = re.search(r"每年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[号日]?", text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        year = _guess_year(month, day, today)
        return "yearly", date(year, month, day).isoformat(), text[:m.start()] + " " + text[m.end():]

    # 每月15号
    m = re.search(r"每月\s*(\d{1,2})\s*[号日]", text)
    if m:
        day = int(m.group(1))
        d = today
        if day < today.day:
            if today.month == 12:
                d = date(today.year + 1, 1, min(day, 31))
            else:
                d = date(today.year, today.month + 1, min(day, 31))
        else:
            d = date(today.year, today.month, min(day, _days_in_month(today.year, today.month)))
        return "monthly", d.isoformat(), text[:m.start()] + " " + text[m.end():]

    # 每周三 / 每周
    m = re.search(r"每周\s*([一二三四五六日天])?", text)
    if m:
        wd_s = m.group(1)
        if wd_s:
            target_wd = WEEKDAY_MAP[wd_s]
            delta = (target_wd - today.weekday()) % 7
            d = today + timedelta(days=delta)
        else:
            d = today
        return "weekly", d.isoformat(), text[:m.start()] + " " + text[m.end():]

    # 每天 / 每日
    m = re.search(r"每天|每日", text)
    if m:
        return "daily", today.isoformat(), text[:m.start()] + " " + text[m.end():]

    return "none", None, text


# ---------------- 预警 & 标题清理 ----------------

_FILLER_WORDS = [
    "记得", "别忘了", "别忘记", "需要", "要去", "要", "得去", "得",
    "准备", "安排", "提醒我", "请", "帮忙", "去", "该", "应该",
]


def _extract_lead(text: str):
    m = re.search(r"提前\s*(\d{1,2})\s*天", text)
    if m:
        n = int(m.group(1))
        leads = sorted(set([1, 3, n])) if n not in (1, 3) else [1, 3]
        return ",".join(str(x) for x in leads), text[:m.start()] + " " + text[m.end():]
    return "1,3", text


def _clean_title(text: str) -> str:
    text = re.sub(r"[，,。、；;：:！!？?“”\"'‘’（）()\[\]【】\s]+", " ", text).strip()
    for w in _FILLER_WORDS:
        if text.startswith(w):
            text = text[len(w):].strip()
    return text.strip(" -·、,，") or "未命名事项"


# ---------------- 主入口 ----------------

def parse_line(line: str, today: date | None = None) -> ParsedEvent:
    today = today or date.today()
    raw = line
    text = line.strip()

    lead_days, text = _extract_lead(text)
    recur, recur_date, text = _extract_recur(text, today)
    # 必须先剥日期再剥时间，否则日期里的数字会被时间规则误吃
    due_date, ambiguous, text, warning = _extract_date(text, today)
    due_time, text = _extract_time(text)
    date_cue = due_date is not None

    # 重复规则已给出锚点日期；否则必须有普通日期
    if due_date is None:
        due_date = recur_date
    if hasattr(due_date, "isoformat"):
        due_date = due_date.isoformat()
    if due_date is None:
        # 完全没识别到日期：默认排到今天，强提示
        due_date = today.isoformat()
        ambiguous = True
        warning = "没识别到日期，已暂定为今天，请在确认表里改"

    title = _clean_title(text)
    has_cue = date_cue or recur != "none" or due_time is not None
    return ParsedEvent(
        title=title,
        due_date=due_date,
        due_time=due_time,
        recur=recur,
        lead_days=lead_days,
        ambiguous=ambiguous,
        raw=raw,
        warnings=[warning] if warning else [],
        has_cue=has_cue,
    )


def parse_text(text: str, today: date | None = None) -> list[ParsedEvent]:
    return [parse_line(line, today) for line in split_events(text)]


# ---------------- 整段合并 ----------------

_HEADING_RE = re.compile(
    r"^\s*(?:\d+\s*[.、)）]|[（(]\s*\d+\s*[)）]|#{1,6}\s*)\s*(.+)$"
)


def looks_like_heading(line: str) -> bool:
    """首行是否像标题：1. / 1、 / 1) / (1) / # 这类编号开头。"""
    return bool(_HEADING_RE.match(line.strip()))


def _strip_heading_number(line: str) -> str:
    m = _HEADING_RE.match(line.strip())
    return m.group(1).strip() if m else line.strip()


# ---------------- 编号分组（1. / 2. 各成一件事） ----------------

_NUMBERED_RE = re.compile(r"^\s*(\d{1,2})\s*[.、)]\s*(.+?)\s*$")


def _dates_in_block(text: str, today: date) -> list[date]:
    """提取一节文字里出现的具体日期（完整年份优先；X月X日按年份上下文推断）。"""
    out: list[date] = []
    year_ctx = None
    years = [int(y) for y in re.findall(r"(\d{4})\s*年", text)]
    cand_years = [y for y in years if today.year - 1 <= y <= today.year + 1]
    if today.year in cand_years:
        year_ctx = today.year
    elif cand_years:
        year_ctx = cand_years[0]

    for m in re.finditer(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})", text):
        try:
            out.append(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        except ValueError:
            pass
    for m in re.finditer(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*[号日]", text):
        mo, da = int(m.group(1)), int(m.group(2))
        if not (1 <= mo <= 12 and 1 <= da <= 31):
            continue
        try:
            if year_ctx:
                out.append(date(year_ctx, mo, da))
            else:
                out.append(date(_guess_year(mo, da, today), mo, da))
        except ValueError:
            pass
    return out


def parse_grouped(text: str, today: date | None = None) -> list[ParsedEvent] | None:
    """按顶层编号（1. / 2. / 1、/ 1)）把一大段切成几件事。

    识别不到至少 2 个递增编号时返回 None，交给调用方走别的模式。
    编号前的前言、最后一节空行之后的共同说明，会并入每件事的备注。
    """
    today = today or date.today()
    lines = text.splitlines()
    heads = []
    for i, ln in enumerate(lines):
        m = _NUMBERED_RE.match(ln.strip())
        if not m:
            continue
        title_part = m.group(2).strip()
        # 排除 "1.5" 之类小数、网址碎片；编号标题要有实质内容
        if len(title_part) < 4 or title_part[0].isdigit():
            continue
        n = int(m.group(1))
        if 1 <= n <= 20:
            heads.append((i, n, title_part))

    if len(heads) < 2:
        return None
    nums = [h[1] for h in heads]
    if any(b < a for a, b in zip(nums, nums[1:])):
        return None

    preamble = "\n".join(lines[:heads[0][0]]).strip()
    blocks: list[tuple[str, str]] = []  # (标题行, 本节正文含标题)
    postamble_lines: list[str] = []

    # "短标题行"（如 简单对比 / 取舍建议）：≤10字、无标点、不是列表项
    punct = set("：:，,。.；;、！!？?（）()【】")

    def _is_subhead(s: str) -> bool:
        s = s.strip()
        return (0 < len(s) <= 10 and not s.startswith(("-", "·", "*"))
                and not any(ch in punct for ch in s))
    for k, (idx, _n, title_part) in enumerate(heads):
        end = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        block_lines = lines[idx:end]
        if k + 1 == len(heads):
            # 最后一节：第一个空行或"短标题行"（简单对比/取舍建议）之后算共同备注
            cut = len(block_lines)
            for j, ln in enumerate(block_lines):
                if j > 0 and (not ln.strip() or _is_subhead(ln)):
                    cut = j
                    break
            postamble_lines = block_lines[cut:]
            block_lines = block_lines[:cut]
        blocks.append((title_part, "\n".join(block_lines).strip()))

    postamble = "\n".join(postamble_lines).strip()
    shared = "\n".join(x for x in (preamble, postamble) if x).strip()

    events: list[ParsedEvent] = []
    for title_part, block in blocks:
        dates = _dates_in_block(block, today)
        upcoming = sorted(d for d in dates if d >= today)
        if upcoming:
            due, ambiguous, warning = upcoming[0], False, None
        elif dates:
            due = max(dates)
            ambiguous, warning = True, "文中日期看起来都已过，请确认绑定日期"
        else:
            due = today
            ambiguous, warning = True, "这一节没识别到具体日期，请用上方日期绑定"

        detail = block
        if shared:
            detail += "\n\n【整体说明】\n" + shared

        events.append(ParsedEvent(
            title=_clean_title(title_part),
            due_date=due.isoformat(),
            ambiguous=ambiguous,
            raw=block,
            warnings=[warning] if warning else [],
            detail=detail,
            has_cue=bool(dates),
        ))
    return events


# ---------------- 结构化文章（Markdown 标题 / 分节编号） ----------------

_MD_HEADER_RE = re.compile(r"^\s*(#{1,6})\s*(.+?)\s*#*\s*$")
_SECTION_NUM_RE = re.compile(r"^\s*[一二三四五六七八九十]{1,2}\s*[、.．]")
_LEAD_NUM_RE = re.compile(r"^\s*(\d{1,2})\s*[.、)]\s*(.+?)\s*$")
_RULE_LINE_RE = re.compile(r"^\s*[-=*_]{3,}\s*$")
_CLOCK_RE = re.compile(r"(?<![\w.])([01]?\d|2[0-4])\s*[:：]\s*([0-5]\d)(?!\d)")
_NEXT_YEAR_HINT_RE = re.compile(r"(次年|明年|下一年)")
_MONTH_ONLY_CTX_RE = re.compile(r"(次年|明年|下一年)?\s*(\d{1,2})\s*月(?!\s*\d|[号日底])")


def _strip_inline_md(s: str) -> str:
    """去掉 **粗体**、__下划线__、`代码` 等行内 Markdown 标记。"""
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"__(.+?)__", r"\1", s)
    return s.replace("`", "").strip()


def _clock_in_block(text: str) -> str | None:
    """节内第一个 hh:mm（24:00 按当天 23:59 处理）；不匹配"3点"等口语时间。"""
    for m in _CLOCK_RE.finditer(text):
        h, minute = int(m.group(1)), int(m.group(2))
        if h == 24:
            h = 23
            minute = 59
        return f"{h:02d}:{minute:02d}"
    return None


def _month_only_dates(block: str, today: date) -> list[date]:
    """节内只有月份没有日的表达（如"次年3月""11月初赛"）→ 月1号，供兜底。"""
    masked = re.sub(r"\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}", " ", block)
    masked = re.sub(r"\d{1,2}\s*月\s*\d{1,2}\s*[号日]", " ", masked)
    masked = re.sub(r"\d{1,2}\s*月\s*底", " ", masked)
    out: list[date] = []
    for m in _MONTH_ONLY_CTX_RE.finditer(masked):
        month = int(m.group(2))
        if not 1 <= month <= 12:
            continue
        if m.group(1):                      # 次年/明年
            year = today.year + 1
        else:
            year = _guess_year(month, 1, today)
        out.append(date(year, month, 1))
    return out


def parse_structured(text: str, today: date | None = None) -> list[ParsedEvent] | None:
    """结构化文章 → 每个标题节一件事（标题=事件名，节内最近未来日期=截止日，
    整节正文进备注）。识别不到结构（<2 个标题节）时返回 None。

    两种结构：
      A. Markdown 标题：最深一级标题出现 ≥2 次即视为事件标题
         （如 ## 大类 / ### 赛事）；
      B. 分节编号："1. …/2. …"，遇到 ## 标题或"一、"分节行时编号允许重新开始。
    """
    today = today or date.today()
    raw_lines = text.splitlines()

    # 每行：(标题级别 0 表示正文, 去标记文本, 原始行)
    recs = []
    for ln in raw_lines:
        s = ln.strip()
        if not s:
            continue
        m = _MD_HEADER_RE.match(s)
        if m:
            recs.append((len(m.group(1)), _strip_inline_md(m.group(2)), s))
        else:
            recs.append((0, _strip_inline_md(s), s))

    # ---- 事件标题定位 ----
    head_idx: list[int] = []
    headers = [(i, lv) for i, (lv, _t, _r) in enumerate(recs) if lv > 0]
    if headers:
        deepest = max(lv for _i, lv in headers)
        heads_a = [i for i, lv in headers if lv == deepest]
        if len(heads_a) >= 2:
            head_idx = heads_a
            mode = "md"
    if not head_idx:
        # 策略 B：分节编号，分节处允许编号重新开始
        heads_b, cur_nums = [], []
        for i, (lv, t, _r) in enumerate(recs):
            if (0 < lv) or _SECTION_NUM_RE.match(t):
                cur_nums = []
                continue
            m = _LEAD_NUM_RE.match(t)
            if not m:
                continue
            title_part = m.group(2).strip()
            if len(title_part) < 4 or title_part[0].isdigit():
                continue
            n = int(m.group(1))
            if not 1 <= n <= 20:
                continue
            if cur_nums and n < cur_nums[-1]:
                continue                  # 节内编号必须非递减
            cur_nums.append(n)
            heads_b.append(i)
        if len(heads_b) >= 2:
            head_idx = heads_b
            mode = "num"
    if not head_idx:
        return None

    head_set = set(head_idx)

    def _block_end(start: int) -> int:
        """下一个事件标题、或更浅一级的分节标题处收束。"""
        h_lv = recs[start][0]
        for k in range(start + 1, len(recs)):
            lv = recs[k][0]
            if k in head_set:
                return k
            if mode == "md" and 0 < lv < h_lv:
                return k
            if mode == "num" and (0 < lv or _SECTION_NUM_RE.match(recs[k][1])):
                return k
        return len(recs)

    events: list[ParsedEvent] = []
    for head in head_idx:
        end = _block_end(head)
        h_lv, h_title, h_raw = recs[head]
        body_recs = recs[head + 1:end]
        norm_lines = [_strip_heading_number(h_title)]
        for _lv, t, _r in body_recs:
            if _RULE_LINE_RE.match(t):
                continue
            norm_lines.append(t)
        block_norm = "\n".join(x for x in norm_lines if x).strip()
        block_raw = "\n".join(
            recs[k][2] for k in range(head, end) if recs[k][2]).strip()

        # 日期：具体日期优先（节内最近未来日），其次"只有月份"兜底，再不行暂定今天
        dates = _dates_in_block(block_norm, today)
        upcoming = sorted(d for d in dates if d >= today)
        ambiguous, warning, has_cue = False, None, bool(dates)
        if upcoming:
            due = upcoming[0]
        elif dates:
            due = max(dates)
            ambiguous, warning = True, "文中日期看起来都已过，请确认绑定日期"
        else:
            months = _month_only_dates(block_norm, today)
            m_up = sorted(d for d in months if d >= today)
            if m_up:
                due = m_up[0]
                ambiguous, warning = True, "只识别到月份、没写具体日期，已暂定为1号，请在确认表里改"
                has_cue = True
            elif months:
                due = max(months)
                ambiguous, warning = True, "月份日期看起来都已过，请确认绑定日期"
                has_cue = True
            else:
                due = today
                ambiguous, warning = True, "这一节没识别到具体日期，请用上方日期绑定"

        due_time = _clock_in_block(block_norm)
        events.append(ParsedEvent(
            title=_clean_title(_strip_heading_number(h_title)),
            due_date=due.isoformat(),
            due_time=due_time,
            ambiguous=ambiguous,
            raw=block_raw,
            warnings=[warning] if warning else [],
            detail=block_norm,
            has_cue=has_cue or due_time is not None,
        ))
    return events


def parse_merged(text: str, today: date | None = None) -> ParsedEvent:
    """整段文字当一件事：首行作标题、全文作备注；时间/重复/预警从全文提取。"""
    today = today or date.today()
    raw = text.strip()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    first = lines[0] if lines else raw

    lead_days, body = _extract_lead(raw)
    recur, recur_date, body = _extract_recur(body, today)
    due_date, ambiguous, _body, warning = _extract_date(body, today)
    due_time, _body = _extract_time(body)
    date_cue = due_date is not None

    if due_date is None:
        due_date = recur_date
    if hasattr(due_date, "isoformat"):
        due_date = due_date.isoformat()
    if due_date is None:
        due_date = today.isoformat()
        ambiguous = True
        warning = "整段里没识别到日期，请用上方日期手动绑定"

    return ParsedEvent(
        title=_clean_title(_strip_heading_number(first)),
        due_date=due_date,
        due_time=due_time,
        recur=recur,
        lead_days=lead_days,
        ambiguous=ambiguous,
        raw=raw,
        warnings=[warning] if warning else [],
        detail=raw,
        has_cue=date_cue or recur != "none" or due_time is not None,
    )


# ================= P0 融合管线：句子编号 + 候选枚举 =================

_CUE_RE = re.compile(
    r"\d|[今明后昨][天日]|周[一二三四五六日天]|每月|每周|每天|月底|次年|下个?月|每年")


def split_sentences(text: str) -> list[Sentence]:
    """换行/句末标点切分 → 短句合并（<10 字且无时间线索向上并）→ 编号 S1..Sn。"""
    merged: list[str] = []
    for s in split_events(text):
        if merged and len(s) < 10 and not _CUE_RE.search(s):
            merged[-1] += s          # 碎片（如"报名方式：官网"）并入上一句
        else:
            merged.append(s)
    return [Sentence(f"S{i+1}", s) for i, s in enumerate(merged)]


_FULL_DATE_RE = re.compile(
    r"(\d{4})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})\s*[号日]?")
_MONTH_DAY_RE = re.compile(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*[号日]")
_MONTH_END_RE = re.compile(r"(\d{1,2})\s*月\s*底")
_N_DAYS_RE = re.compile(r"(\d{1,3})\s*天(?:以后|之后|过后|后)")
_WEEKDAY_RE = re.compile(
    r"(下下|下个|下|本|这个|这)?\s*(?:周|星期|礼拜)\s*([一二三四五六日天])")
_MONTH_ONLY_RE = re.compile(r"(?<!\d)(\d{1,2})\s*月(?!\s*\d)")
_RELATIVE_WORDS = [("大后天", 3), ("后天", 2), ("明天", 1), ("明日", 1),
                   ("今天", 0), ("今日", 0), ("今晚", 0)]


def _safe_date(y: int, m: int, d: int):
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _scan_dates(text: str, today: date) -> list[tuple[str, str, bool]]:
    """全文枚举日期候选，按优先级扫描，已占用的原文 span 不再重复产候选。
    返回 [(原文, ISO日期, ambiguous)]。歧义日期（只有月份）只给最优解析并标 ambiguous。"""
    out: list[tuple[str, str, bool]] = []
    used: list[tuple[int, int]] = []

    def _free(a, b):
        return all(b <= s or a >= e for s, e in used)

    for m in _FULL_DATE_RE.finditer(text):
        if not _free(m.start(), m.end()):
            continue
        d = _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d:
            used.append((m.start(), m.end()))
            out.append((m.group(0).strip(), d.isoformat(), False))

    for m in _MONTH_END_RE.finditer(text):
        if not _free(m.start(), m.end()):
            continue
        month = int(m.group(1))
        if not 1 <= month <= 12:
            continue
        year = _guess_year(month, 28, today)
        d = _safe_date(year, month, _days_in_month(year, month))
        if d:
            used.append((m.start(), m.end()))
            out.append((m.group(0).strip(), d.isoformat(), False))

    for m in _MONTH_DAY_RE.finditer(text):
        if not _free(m.start(), m.end()):
            continue
        mo, da = int(m.group(1)), int(m.group(2))
        if not (1 <= mo <= 12 and 1 <= da <= 31):
            continue
        d = _safe_date(_guess_year(mo, da, today), mo, da)
        if d:
            used.append((m.start(), m.end()))
            out.append((m.group(0).strip(), d.isoformat(), False))

    for m in _N_DAYS_RE.finditer(text):
        if not _free(m.start(), m.end()):
            continue
        d = today + timedelta(days=int(m.group(1)))
        used.append((m.start(), m.end()))
        out.append((m.group(0).strip(), d.isoformat(), False))

    for word, delta in _RELATIVE_WORDS:
        idx = text.find(word)
        if idx >= 0 and _free(idx, idx + len(word)):
            d = today + timedelta(days=delta)
            used.append((idx, idx + len(word)))
            out.append((word, d.isoformat(), False))

    for m in _WEEKDAY_RE.finditer(text):
        if not _free(m.start(), m.end()):
            continue
        prefix, wd_s = m.group(1), m.group(2)
        target_wd = WEEKDAY_MAP[wd_s]
        base = today - timedelta(days=today.weekday())
        if prefix in ("下下", "下个下"):
            base += timedelta(weeks=2)
        elif prefix in ("下", "下个"):
            base += timedelta(weeks=1)
        d = base + timedelta(days=target_wd)
        if prefix in (None, "本", "这", "这个") and d < today:
            d += timedelta(weeks=1)
        used.append((m.start(), m.end()))
        out.append((m.group(0).strip(), d.isoformat(), False))

    # 只有 X 月（没日）→ 最优解析为该月 1 号，标 ambiguous（裁决归算法/P3）
    for m in _MONTH_ONLY_RE.finditer(text):
        if not _free(m.start(), m.end()):
            continue
        month = int(m.group(1))
        if not 1 <= month <= 12:
            continue
        d = _safe_date(_guess_year(month, 1, today), month, 1)
        if d:
            used.append((m.start(), m.end()))
            out.append((m.group(0).strip(), d.isoformat(), True))

    return out


_TIME_RE = re.compile(
    r"(上午|早上|早晨|清晨|凌晨|中午|下午|傍晚|晚上|晚)?"
    r"\s*(\d{1,2})\s*[:：点]\s*(?:(\d{1,2}|半)\s*分?)?")


def _scan_times(text: str) -> list[tuple[str, str]]:
    """全文枚举时间候选，返回 [(原文, HH:MM)]，同一解析值去重。"""
    out, seen = [], set()
    for m in _TIME_RE.finditer(text):
        period, h_s, m_s = m.group(1), m.group(2), m.group(3)
        h, minute = int(h_s), 0
        if m_s == "半":
            minute = 30
        elif m_s:
            minute = int(m_s)
        if h > 24 or minute > 59:
            continue
        if period in _PERIOD_PM and h < 12:
            h += 12
        elif period == "中午" and h < 11:
            h += 12
        elif period in _PERIOD_AM and h == 12:
            h = 0
        v = f"{h:02d}:{minute:02d}"
        if v in seen:
            continue
        seen.add(v)
        out.append((m.group(0).strip(), v))
    return out


_RECUR_YEAR_RE = re.compile(r"每年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[号日]?")
_RECUR_MONTH_RE = re.compile(r"每月\s*(\d{1,2})\s*[号日]")
_RECUR_WEEK_RE = re.compile(r"每周\s*([一二三四五六日天])?")
_RECUR_DAY_RE = re.compile(r"每天|每日")


def _scan_recurs(text: str, today: date) -> list[tuple[str, str]]:
    """全文枚举重复规则候选，返回 [(原文, recur_key)]。"""
    out = []
    for m in _RECUR_YEAR_RE.finditer(text):
        out.append((m.group(0).strip(), "yearly"))
    for m in _RECUR_MONTH_RE.finditer(text):
        out.append((m.group(0).strip(), "monthly"))
    for m in _RECUR_WEEK_RE.finditer(text):
        out.append((m.group(0).strip(), "weekly"))
    for m in _RECUR_DAY_RE.finditer(text):
        out.append((m.group(0).strip(), "daily"))
    # 去重（同一 raw+key 只留一个）
    seen, dedup = set(), []
    for raw, v in out:
        k = (raw, v)
        if k not in seen:
            seen.add(k)
            dedup.append((raw, v))
    return dedup


# ---------------- 非事项内容判定（属性行/元叙述） ----------------

# 属性标签行："**截止时间**：10月16日"、"费用：200元"、"- 方向：机器学习"
_PROPERTY_LABELS = {
    "截止时间", "报名截止", "报名时间", "报名窗口", "报名入口", "官方入口",
    "报名方式", "投递方式", "比赛时间", "比赛地点", "费用", "方向", "含金量",
    "适配建议", "官网", "入口", "链接", "网址", "时间", "地点", "联系人",
    "备注", "说明", "简介", "主办方", "主办", "提交方式", "组队", "赛道",
}
_PROPERTY_LABEL_RE = re.compile(
    r"^[\s\-*·•#]*[【]?([\u4e00-\u9fa5A-Za-z]{2,10})[】]?\s*[：:]")
# 元叙述/编辑性文字：不是给日历用的行动事项
_META_PREFIX_RE = re.compile(
    r"^[\s\-*·•#]*(?:"
    r"第[一二三四五六七八九十]+优先级|最终优先级|优先级排序|已过期|已剔除|"
    r"按.{0,16}排序|需要我|理由[：:]|更新至|覆盖[AI计算机数学网络学术].{0,10}方向|"
    r"[✅❌✔✘×])")
_META_SUFFIX_RE = re.compile(r"(吗[？?]|整理出来|直接整理)")


def is_junk_content_line(text: str) -> bool:
    """属性行（费用/方向/含金量…）或元叙述（排序说明/提问/优先级），不是独立事项。"""
    s = (text or "").strip()
    if not s:
        return True
    plain = _strip_inline_md(s)
    m = _PROPERTY_LABEL_RE.match(plain)
    if m and m.group(1) in _PROPERTY_LABELS:
        return True
    if _META_PREFIX_RE.match(plain) or _META_SUFFIX_RE.search(plain):
        return True
    return False


def parse_pipeline(text: str, today: date | None = None) -> PipelineResult:
    """P0：算法先行，毫秒级产出句子编号、D/T/R 候选与规则事件（含来源句子）。"""
    today = today or date.today()
    sents = split_sentences(text)
    body = "\n".join(s.text for s in sents)
    date_cands = [Candidate(f"D{i+1}", *c)
                  for i, c in enumerate(_scan_dates(body, today))]
    time_cands = [Candidate(f"T{i+1}", *c)
                  for i, c in enumerate(_scan_times(body))]
    recur_cands = [Candidate(f"R{i+1}", *c)
                   for i, c in enumerate(_scan_recurs(body, today))]

    # 规则事件择优：结构化标题节 > 编号分组 > 整段合并 > 逐行
    structured = parse_structured(text, today)
    grouped = None if structured is not None else parse_grouped(text, today)
    chunks = split_events(text)
    line_events = []
    for s in sents:
        ev = parse_line(s.text, today)
        # 无任何时间线索的属性行/元叙述不独立成事件（其信息归属于所属标题节）
        if not ev.has_cue and is_junk_content_line(s.text):
            continue
        line_events.append(ev)
    cues = sum(1 for ev in line_events if ev.has_cue)
    merge = (structured is None and grouped is None and len(chunks) >= 2
             and cues <= 1 and looks_like_heading(chunks[0]))

    if structured is not None:
        events = structured
    elif grouped is not None:
        events = grouped
    elif merge:
        events = [parse_merged(text, today)]
    else:
        events = line_events

    # 来源句子映射：event.raw 覆盖到的句子
    for ev in events:
        if not ev.source_sentences:
            sids = [s.sid for s in sents if s.text and s.text in (ev.raw or "")]
            ev.source_sentences = sids or ([sents[0].sid] if sents else [])

    return PipelineResult(sents, date_cands, time_cands, recur_cands, events)
