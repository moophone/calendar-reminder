# -*- coding: utf-8 -*-
"""P2 匹配合一 + P3 终检（纯算法，见 AI算法融合参考 §0.4/§0.5）。

P0 = text_parser.parse_pipeline（句子编号/候选/规则事件，毫秒级）
P1 = llm_fallback.extract_selection（AI 选择器：分句分组、起标题、选 ID）
本模块把两者合一：配对合并、规则独有保留、AI 独有两档，最后 P3 终检。
日期/时间/重复的数值一律以规则侧为准，模型只贡献标题、备注与分组。
"""
import difflib
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from text_parser import ParsedEvent, is_junk_content_line

_RECUR_OK = {"none", "daily", "weekly", "monthly", "yearly"}


def _junk_ai_title(title: str) -> bool:
    """小模型常见的垃圾标题：属性行/元叙述、残句（句尾带标点）、过短碎片。"""
    t = (title or "").strip()
    if len(t) <= 3:
        return True
    if is_junk_content_line(t):
        return True
    # 残句特征：以句读/连词收尾（如"术证明。"、"注册费，线下参会"）
    if t[-1] in "。；;，,、的":
        return True
    return False

# ---------------- 相似度 ----------------

def _bigrams(s: str) -> set:
    keep = [c for c in s if c.isalnum() or '一' <= c <= '鿿']
    return {keep[i] + keep[i + 1] for i in range(len(keep) - 1)}


def title_sim(a: str, b: str) -> float:
    """bigram Jaccard 与归一化编辑距离（difflib ratio）取均值。"""
    a, b = a or "", b or ""
    if not a or not b:
        return 0.0
    ba, bb = _bigrams(a), _bigrams(b)
    union = ba | bb
    jac = len(ba & bb) / len(union) if union else 0.0
    return (jac + difflib.SequenceMatcher(None, a, b).ratio()) / 2


def overlap_score(rule_sids: list, ai_sids: list) -> float:
    """句子 ID 重叠 = max(Jaccard, |交|/min(|A|,|B|))。

    Jaccard 处理两边粒度相当；重叠系数处理 AI 是规则超集
    （规则抓 1 句、AI 合并 3 句时 Jaccard=0.33 而重叠系数=1.0）。"""
    A, B = set(rule_sids or []), set(ai_sids or [])
    if not A or not B:
        return 0.0
    inter = len(A & B)
    return max(inter / len(A | B), inter / min(len(A), len(B)))


# ---------------- ID 白名单 ----------------

def _resolve_id(idv, n: int, prefix: str) -> str | None:
    """越界/非法 ID → None（不修补，直接降级为 null）。"""
    if not isinstance(idv, str):
        return None
    m = re.fullmatch(rf"{prefix}(\d+)", idv.strip())
    if m and 1 <= int(m.group(1)) <= n:
        return idv.strip()
    return None


def _cand_val(cands: list, idv: str | None):
    if not idv:
        return None
    return cands[int(idv[1:]) - 1]


def _cand_agree(ev: ParsedEvent, a: dict, date_cands, time_cands, recur_cands) -> float:
    """候选一致分：AI 选的 D/T/R 解析值与规则事件字段一致的占比。"""
    hits, total = 0, 0
    for cands, idv, rule_v in (
            (date_cands, a["d"], ev.due_date),
            (time_cands, a["t"], ev.due_time),
            (recur_cands, a["r"], ev.recur if ev.recur != "none" else None)):
        total += 1
        c = _cand_val(cands, idv)
        if c and rule_v and c.value == rule_v:
            hits += 1
    return hits / total


# ---------------- P2 匹配 ----------------

def match(rule_events: list, ai_items: list, date_cands: list, time_cands: list,
          recur_cands: list, valid_sids: set, user_edited: dict | None,
          today: date, n_chunks: int = 1) -> tuple[list, dict]:
    """P2 匹配合一 + P3 终检。

    rule_events: P0 规则事件（ParsedEvent，带 source_sentences）
    ai_items:    P1 选择器行 {sentences,title,date_id,time_id,recur_id,detail}
    valid_sids:  P0 句子集合（SID 校验用）
    user_edited: {规则条索引: {"title","detail",...}}，被编辑字段不用 AI 值覆盖
    返回 (合并后 ParsedEvent 列表, 统计 dict)。AI 独有条带 ai_new/auto_checked 属性。"""
    t0 = time.time()
    user_edited = user_edited or {}

    # ---- P3 前置：SID 校验 + ID 白名单 ----
    ai_rows = []
    for it in ai_items or []:
        sids = {s for s in (it.get("sentences") or []) if s in valid_sids}
        title = str(it.get("title", "")).strip()
        if not sids or not title:
            continue                      # SID 校验失败 / 空标题 → 整条丢弃
        ai_rows.append({
            "sids": sids,
            "title": title,
            "detail": str(it.get("detail", "")).strip(),
            "d": _resolve_id(it.get("date_id"), len(date_cands), "D"),
            "t": _resolve_id(it.get("time_id"), len(time_cands), "T"),
            "r": _resolve_id(it.get("recur_id"), len(recur_cands), "R"),
        })

    # ---- 综合分 0.6*句子重叠 + 0.3*标题相似 + 0.1*候选一致，降序贪心一对一 ----
    scored = []
    for j, a in enumerate(ai_rows):
        for i, ev in enumerate(rule_events):
            ov = overlap_score(ev.source_sentences, list(a["sids"]))
            ts = title_sim((ev.title or "") + (ev.detail or ""),
                           a["title"] + a["detail"])
            ca = _cand_agree(ev, a, date_cands, time_cands, recur_cands)
            score = 0.6 * ov + 0.3 * ts + 0.1 * ca
            if score >= 0.4:
                scored.append((score, i, j))
    scored.sort(key=lambda x: (-x[0], x[1], x[2]))
    used_r: set[int] = set()
    matched: dict[int, tuple[int, float]] = {}
    for score, i, j in scored:
        if i in used_r or j in matched:
            continue
        used_r.add(i)
        matched[i] = (j, score)

    # ---- 配对合并 + 规则独有保留 ----
    out: list[ParsedEvent] = []
    n_title_opt = n_detail_opt = 0

    def _sid_key(s: str):
        return int(s[1:]) if s[1:].isdigit() else 0

    for i, ev in enumerate(rule_events):
        if i not in matched:
            out.append(ev)                # 规则独有 → 原样保留
            continue
        j, score = matched[i]
        a = ai_rows[j]
        src = sorted({*(ev.source_sentences or []), *a["sids"]}, key=_sid_key)
        merged = ParsedEvent(
            title=ev.title, due_date=ev.due_date, due_time=ev.due_time,
            recur=ev.recur, lead_days=ev.lead_days, ambiguous=ev.ambiguous,
            raw=ev.raw, warnings=list(ev.warnings or []), detail=ev.detail,
            has_cue=ev.has_cue, source_sentences=src)
        # 规则缺失时用 AI 选的候选解析值补位
        dc = _cand_val(date_cands, a["d"])
        if not merged.due_date and dc:
            merged.due_date = dc.value
            merged.ambiguous = dc.ambiguous
        tc = _cand_val(time_cands, a["t"])
        if not merged.due_time and tc:
            merged.due_time = tc.value
        rc = _cand_val(recur_cands, a["r"])
        if merged.recur in (None, "none") and rc and rc.value in _RECUR_OK:
            merged.recur = rc.value
        # 标题/备注用 AI 值；用户编辑过的字段、垃圾标题跳过
        skip = user_edited.get(i, set())
        if ("title" not in skip and a["title"] != merged.title
                and not _junk_ai_title(a["title"])):
            merged.title = a["title"]
            n_title_opt += 1
        if "detail" not in skip and a["detail"] and a["detail"] != merged.detail:
            merged.detail = a["detail"]
            n_detail_opt += 1
        merged.paired_score = round(score, 3)
        out.append(merged)

    # ---- AI 独有（两档）----
    n_ai_new_auto = n_ai_junk = n_ai_new = 0
    used_a = {j for j, _ in matched.values()}
    for j, a in enumerate(ai_rows):
        if j in used_a:
            continue
        if _junk_ai_title(a["title"]):
            n_ai_junk += 1            # 属性行/元叙述/残句 → 丢弃，不进确认表
            continue
        dc = _cand_val(date_cands, a["d"])
        due = dc.value if dc else None
        auto = bool(due and dc and not dc.ambiguous
                    and date.fromisoformat(due) >= today + timedelta(days=1))
        tc = _cand_val(time_cands, a["t"])
        rc = _cand_val(recur_cands, a["r"])
        ev = ParsedEvent(
            title=a["title"], due_date=due,
            due_time=tc.value if tc else None,
            recur=rc.value if rc and rc.value in _RECUR_OK else "none",
            ambiguous=dc.ambiguous if dc else False,
            raw="", warnings=[], detail=a["detail"],
            has_cue=bool(due or tc or rc),
            source_sentences=sorted(a["sids"], key=_sid_key))
        ev.ai_new = True
        ev.auto_checked = auto            # False 档标红"需人工确认"
        out.append(ev)
        n_ai_new += 1
        if auto:
            n_ai_new_auto += 1

    # ---- P3 终检 ----
    final: list[ParsedEvent] = []
    for ev in out:
        t = (ev.title or "").strip()
        if not t:
            continue                      # 空标题丢弃
        ev.title = t[:100]
        ev.detail = (ev.detail or "")[:600]
        if ev.recur not in _RECUR_OK:
            ev.recur = "none"
        if ev.due_date:
            try:
                if date.fromisoformat(ev.due_date) < today:
                    ev.warnings = list(ev.warnings or []) + ["日期已过去，请确认"]
            except ValueError:
                ev.warnings = list(ev.warnings or []) + ["日期格式异常，请确认"]
        final.append(ev)

    stats = {
        "chunks": n_chunks, "ai_in": len(ai_items or []),
        "ai_kept": len(ai_rows), "paired": len(matched),
        "rule_only": len(rule_events) - len(matched),
        "ai_new": n_ai_new, "ai_new_auto": n_ai_new_auto,
        "ai_junk": n_ai_junk,
        "title_opt": n_title_opt, "detail_opt": n_detail_opt,
        "elapsed": round(time.time() - t0, 2),
    }
    _write_log(stats)
    return final, stats


def dedup_chunks(rows: list) -> list:
    """跨块去重（规格 §0.6）：块间重叠 1 句会让同一事件被两块各出一条。

    合并条件收紧：共享 SID ≥ 2 且标题相似 ≥ 0.5；或共享重叠句且标题相似 ≥ 0.7。
    命中保留信息多的一条（detail 更长者补齐）。"""
    out: list = []
    for r in rows or []:
        sids = set(r.get("sentences") or [])
        hit = None
        for o in out:
            shared = sids & set(o.get("sentences") or [])
            if not shared:
                continue
            sim = title_sim(o.get("title", ""), r.get("title", ""))
            if (len(shared) >= 2 and sim >= 0.5) or sim >= 0.7:
                hit = o
                break
        if hit is None:
            out.append(dict(r))
        elif len(r.get("detail", "")) > len(hit.get("detail", "")):
            hit["detail"] = r["detail"]
    return out


# ---------------- 解析日志 ----------------

if getattr(sys, "frozen", False):
    _LOG_DIR = Path(sys.executable).resolve().parent / "logs"
else:
    _LOG_DIR = Path(__file__).resolve().parent / "logs"


def _write_log(stats: dict):
    try:
        _LOG_DIR.mkdir(exist_ok=True)
        p = _LOG_DIR / f"parse_{date.today():%Y%m%d}.log"
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] "
                    + " ".join(f"{k}={v}" for k, v in stats.items()) + "\n")
    except Exception:
        pass
