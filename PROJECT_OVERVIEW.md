# CalendarReminder 项目全览

> 用途：交给任意 AI / 设计师，让它了解项目全貌后重新设计布局、功能与 UI。

## 1. 基本信息

| 项 | 值 |
|---|---|
| 项目路径 | `D:\CalendarReminder` |
| 技术栈 | Python 3.13 + PyQt6 6.11 + SQLite（标准库） |
| 运行环境 | Windows，纯本地离线，无网络依赖 |
| 启动入口 | `python main.py` |
| 测试 | `python -m unittest discover -s tests`（38 项，标准库 unittest，无 pytest） |
| 外部依赖 | PyQt6（唯一第三方库），其余全用标准库 |

## 2. 目录结构

```
D:\CalendarReminder\
├── main.py              # 入口：CalendarApp（悬浮球 + 主窗口 + 托盘提醒）
├── ui.py                # 全部 UI（68KB，主窗口/弹窗/导入框/编辑框/日历/悬浮球）
├── style.py             # 全局配色 + QSS 样式表
├── storage.py           # SQLite 数据层（建表/迁移/CRUD/重复规则推算）
├── reminders.py         # 提醒引擎（到期检测/长期每日/人话格式化/弹窗触发）
├── countdown.py         # 倒计时纯函数（自然月加法，§3.1 规格）
├── text_parser.py       # P0 规则解析器（中文自由文本→事项列表）
├── parse_pipeline.py    # P2/P3 匹配+终检（规则结果与 AI 结果合流）
├── llm_fallback.py      # 本地小模型选择器（P1：GBNF+JSONL，只选ID不算日期）
├── autostart.py         # 开机自启（注册表写入/删除）
├── countdown.py         # 倒计时计算
├── models/
│   └── Qwen3-1.7B-Q8_0.gguf   # 本地模型（1.83GB）
├── llama/
│   └── llama-server.exe       # llama.cpp HTTP 服务（OpenAI 兼容）
├── tests/
│   ├── test_countdown.py      # 9 测
│   ├── test_storage_migrate.py # 5 测
│   ├── test_p0.py             # 11 测
│   └── test_p2.py             # 13 测
├── data/
│   └── calendar.db            # SQLite 数据库
├── logs/
│   └── parse_YYYYMMDD.log    # AI 解析日志
└── docs/
    ├── AI算法融合参考.md          # 四阶段管线设计（评审参考）
    └── superpowers/
        ├── specs/2026-10-09-redesign-design.md    # v6 设计规格
        └── plans/2026-10-09-redesign-implementation.md  # 实施计划+偏差登记
```

## 3. 数据库 Schema

### tasks 表（主表）
```sql
CREATE TABLE tasks (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    title            TEXT    NOT NULL,
    detail           TEXT    DEFAULT '',
    due_date         TEXT,              -- YYYY-MM-DD，可为 NULL（无目标日）
    due_time         TEXT,              -- HH:MM，可为 NULL
    recur            TEXT    DEFAULT 'none',   -- none/daily/weekly/monthly/yearly
    lead_days        TEXT    DEFAULT '1,3',   -- 预警天数（逗号分隔）
    status           TEXT    DEFAULT 'pending', -- pending/done
    created_at       TEXT    NOT NULL,
    completed_at     TEXT,
    task_type        TEXT    DEFAULT 'short',   -- short/long
    last_notified_at DATE                        -- 长期任务每日提醒标记
);
-- 索引：(status, task_type, due_date)
```

### journals 表（历程记录）
```sql
CREATE TABLE journals (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    INTEGER NOT NULL,
    content    TEXT    NOT NULL,
    created_at TEXT    NOT NULL,
    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
);
```

### reminder_log 表（重复任务已提醒记录）
```sql
CREATE TABLE reminder_log (
    task_id  INTEGER NOT NULL,
    log_date TEXT    NOT NULL,
    shown_at TEXT    NOT NULL,
    PRIMARY KEY (task_id, log_date)
);
```

### settings 表（键值配置）
```sql
CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
```

### 当前数据
```
id=15  SAIS 科学智能大赛               2027-03-01  short  pending
id=16  全球大学生生命科学挑战赛          2026-10-10  short  pending
id=17  江苏AI+科学与工程创新实践黑客松   2026-10-10  short  pending
```

## 4. 模块职责与接口

### storage.py — 数据层
| 函数 | 作用 |
|---|---|
| `add_task(title, due_date, due_time, detail, recur, lead_days, task_type)` | 新增任务，返回 id |
| `get_task(task_id)` | 获取单条 |
| `all_tasks(include_done=False)` | 全部（默认不含已完成） |
| `panel_tasks(today=None)` | 面板展示：长期全部 + 短期 7 天窗口 |
| `long_tasks_for_daily(today)` | 长期任务中 last_notified_at < today 的 |
| `mark_daily_notified(task_ids)` | 批量标记已通知 |
| `complete_task(task_id, today)` / `reopen_task(task_id)` | 完成/重开 |
| `delete_task(task_id)` | 删除（级联删 journals） |
| `add_journal(task_id, content)` / `journals_of(task_id)` | 历程 CRUD |
| `next_occurrence(due, recur, ref)` | 重复规则推算：返回 >= ref 的最近发生日期 |
| `DB_PATH` | 数据库路径，默认 `data/calendar.db` |

### reminders.py — 提醒引擎
| 类/函数 | 作用 |
|---|---|
| `ReminderItem` | 数据类：task(dict), eff_date(date), kind(str), days(int) |
| `human_explain(task, today=None)` | 人话格式化："今天到期 · 提前 1/3 天预警" |
| `countdown_badge(task)` | 返回 (text, bg, fg) 徽标样式 |
| `collect_due(today=None)` | 短期到期检测：今天/逾期/预警/今日重复 |
| `collect_long_daily(today=None)` | 长期每日通道 |
| `today_brief(today=None)` | 返回 (today_count, overdue_count) |
| `ReminderEngine(QObject)` | 信号 `items_ready(list[ReminderItem])`；`check(force)` / `check_long_daily(force)` / `snooze(items, minutes)` |

### text_parser.py — P0 规则解析
| 类/函数 | 作用 |
|---|---|
| `ParsedEvent` | 规则事件：title/due_date/due_time/recur/ambiguous/raw/warnings/source_sentences |
| `Sentence` | sid("S1") + text |
| `Candidate` | cid("D1") + raw + value + ambiguous |
| `PipelineResult` | sentences + date_cands + time_cands + recur_cands + events |
| `split_sentences(text)` | 分句+短句合并 |
| `parse_pipeline(text, today)` | 完整 P0 管线 → PipelineResult |

### parse_pipeline.py — P2/P3 匹配+终检
| 函数 | 作用 |
|---|---|
| `match(rule_events, ai_items, ...)` | 句子重叠配对+全局一对一+用户编辑优先→合并后的 events + stats |
| `dedup_chunks(rows)` | 跨块去重（共享 SID + 标题相似度） |
| `_write_log(stats)` | 写解析日志到 logs/parse_YYYYMMDD.log |

### llm_fallback.py — 本地模型选择器
| 函数 | 作用 |
|---|---|
| `is_available()` | 检查 llama-server.exe + .gguf 是否存在 |
| `preload()` | 后台线程延迟 2s 启动 server + 探针 |
| `extract_selection(sents, date_cands, time_cands, recur_cands)` | P1：模型只输出 sentences/title/date_id/time_id/recur_id/detail |
| `build_grammar(n_dates, n_times, n_recurs)` | 生成 GBNF 约束（ID 枚举） |
| `probe_grammar()` | 启动探针：确认 grammar 约束生效 |
| `chunk_sentences(sents, max_chars)` | 长文本分块（块间重叠 1 句） |

### countdown.py — 倒计时纯函数
| 函数 | 作用 |
|---|---|
| `countdown(start, end)` | 自然月加法：≥1月→"X月X天"；≥1周→"X周X天"；≥1天→"X天[X小时]"；<1天→"X小时" |

### style.py — 配色与样式
```
配色（暖纸手账风格）：
  BG=#faf6ef      CARD=#fffdf8     TEXT=#4a3a24    TEXT_SUB=#8b7a5e
  ACCENT=#e07b39   ACCENT_DARK=#c9682b   DANGER=#b03434
  BORDER=#ecdfc9
  LONG_BG=#f3e8d5  LONG_FG=#8a6a34    （长期：深金）
  NEAR_BG=#fde3c8   NEAR_FG=#b3591c    （临近：橙）
  URGENT_BG=#fadadb URGENT_FG=#b03434  （紧急：红）
  TODAY_BG=#fdeeda  SEL_BG=#fbe3cf    HOVER_BG=#f7ead9
```
QSS 覆盖：QWidget/QPushButton/QCalendarWidget/QListWidget/QCheckBox/QScrollBar/QMenu/QDialog

### autostart.py — 开机自启
| 函数 | 作用 |
|---|---|
| `add()` | 写注册表 `HKCU\...\Run\CalendarReminder` |
| `remove()` | 删注册表项 |

## 5. UI 结构（ui.py，68KB，单文件）

### 类清单
| 类 | 行号范围 | 作用 |
|---|---|---|
| `DotCalendar(QCalendarWidget)` | ~230-260 | 日历：日期格画圆点（已完成绿/待办橙） |
| `TaskCard(QWidget)` | ~264-357 | 待办面板中的单条卡片：checkbox+标题+倒计时徽标+说明；mousePressEvent→clicked 信号 |
| `TaskDialog(QDialog)` | ~362-510 | 新建/编辑事项弹窗：标题/日期/时间/重复/预警/类型/备注 |
| `ImportDialog(QDialog)` | ~520-920 | 导入确认弹窗：6列表格(导入/日期/时间/重复/类型/事项)+"智能解析"按钮+AI分块进度+两档标色+全选/全不选 |
| `FloatBall(QWidget)` | ~1080-1195 | 桌面悬浮球：左键显/隐主窗口、右键菜单(检查/导入/新建/今天/退出)、拖动、check_requested 信号 |
| `ReminderPopup(QWidget)` | ~965-1075 | 非模态置顶提醒弹窗：任务列表+完成按钮+"稍后10分钟"+"知道了"+右上×关闭+Esc关闭 |
| `MainWindow(QWidget)` | ~1270-1700 | 主窗口 |

### MainWindow 布局（v6 镜像布局）
```
┌─────────────────────────────────────────────────┐
│ [日历提醒]  [导入文本] [新建] [今天] [⚙菜单]      │  顶栏
├──────────────────┬──────────────────────────────┤
│  左列 (1/2)      │  右列 (1/2)                   │
│  ┌────────────┐  │  待办  共N条·长期X·短期Y       │
│  │  小日历     │  │  ┌──────────────────────┐    │
│  │  (DotCal)  │  │  │ 长期 · 常驻（X）      │    │
│  └────────────┘  │  │ [□ 任务卡]            │    │
│  ┌────────────┐  │  │ [□ 任务卡]            │    │
│  │  详情卡     │  │  │ [展开全部]            │    │
│  │  (空白态/   │  │  ├──────────────────────┤    │
│  │   详情)    │  │  │ 短期 · 7天内（Y）    │    │
│  │            │  │  │ [□ 任务卡]            │    │
│  │  [⋯ 操作]  │  │  │ [□ 任务卡]            │    │
│  └────────────┘  │  └──────────────────────┘    │
└──────────────────┴──────────────────────────────┘
```

### 详情卡内容（从上到下）
1. 标题（`d_title`）+ 收起按钮（`btn_collapse` ×）
2. 标签行：长期/短期 · 目标日 · 重复规则
3. 大号倒计时（`d_countdown`，22px 粗体）
4. 人话说明（`d_explain`）
5. 详情文本区（`d_detail`，QTextEdit 只读，可滚动）
6. 操作区（`d_actions`）：单"⋯"按钮 → 弹出菜单（标记完成/编辑/记下历程/删除）
7. 历程列表（`d_journals`，默认折叠）+ 输入框（默认折叠）

### 空白态
- 未选中任务时显示"选择左侧事项查看详情"
- 详情/操作/历程全部隐藏
- 微信电脑版风格：不点不选中

### 交互流程
1. 点右列任务卡 → `select_task(task_id)` → 详情卡显示该任务
2. 点日历日期 → 高亮该日到期行 + 自动选中第一条匹配任务
3. Esc / 点空白处 → 回到空白态
4. "⋯"菜单 → 标记完成/编辑/展开历程/删除

## 6. 四阶段解析管线

### P0 规则解析（毫秒级，纯算法）
- 输入：整段中文文本
- 分句：按换行/编号/句号切分 + 短句合并（<10字且无时间线索并入上句）
- 候选枚举：日期 D1..Dn / 时间 T1..Tm / 重复 R1..Rk
- 事件提取：正则+中文日期词库，支持绝对/相对/周X/N天后/X月底/次年
- 歧义标记：只给最优解析一个候选，标 ambiguous=True
- 结果立即显示在导入表格

### P1 AI 选择器（5-8秒，后台线程）
- 本地 Qwen3-1.7B（Q8_0）通过 llama-server HTTP 接口
- 模型只做：分句分组、起标题、选 ID
- GBNF 硬约束：ID 只能取枚举值或 null
- JSONL 二级防线：逐行 json.loads，失败行丢弃
- 启动探针：确认 grammar 生效，失败则降级
- 失败重试一次（截半输入，保留全局 SID）

### P2 匹配合一（纯算法）
- 主匹配：句子 ID 重叠 max(Jaccard, 重叠系数)
- 辅匹配：标题 bigram + 编辑距离
- 综合分：0.6*句子重叠 + 0.3*标题相似 + 0.1*候选一致
- 全局一对一：按分数降序贪心
- 合并：日期/时间/重复用规则值；标题/备注用 AI 值
- 用户编辑优先：P1 期间用户改过的字段不被 AI 覆盖
- AI 独有分两档：有效未来日期+非ambiguous→自动勾选标黄；否则不勾选标红

### P3 终检（纯算法）
- SID 校验：AI 事件的 sentences 至少一个在 P0 集合中，否则丢弃
- ID 白名单：越界 ID 降级为 null
- 长度硬切：title≤100, detail≤600
- 空标题丢弃
- 重复白名单：recur 只能是 none/daily/weekly/monthly/yearly
- 过去日期/格式异常加 warnings

## 7. 配色系统

暖纸手账风格：
- 主背景：暖米白 `#faf6ef`
- 卡片：近白 `#fffdf8`
- 主文字：深棕 `#4a3a24`
- 次要文字：暖灰 `#8b7a5e`
- 强调色：暖橙 `#e07b39` / 深橙 `#c9682b`
- 危险/逾期：暗红 `#b03434`
- 边框：浅米 `#ecdfc9`
- 长期徽标：深金底+棕字
- 临近徽标：浅橙底+橙字
- 紧急徽标：浅红底+红字
- 日历圆点：已完成暖灰、重复暖绿

## 8. 当前已知问题

1. **点击任务卡有时不触发选中**：TaskCard 的 label 子控件虽加了 `WA_TransparentForMouseEvents`，但可能在某些点击区域仍不触发 `mousePressEvent`
2. **日历点击体验**：点日期会高亮+选中第一条匹配，但如果该日无任务则只有高亮变化，详情卡不变
3. **弹窗×按钮位置**：在右上角，可能不够显眼
4. **详情卡内容区高度**：取决于左列整体高度，在窗口较小时可能仍偏小
5. **UI 单文件过大**：ui.py 68KB，所有 UI 类挤在一个文件
6. **无响应式适配**：固定布局，不同分辨率下表现可能不一致

## 9. 规格偏差登记（7条）

1. 索引列用 `(status, task_type, due_date)` 替代设计文档的 `(task_type, due_date, is_archived)`——tasks 表无 is_archived 列
2. 长期任务"稍后10分钟"仅内存 snooze，不回写 last_notified_at
3. `extract_selection(sents)` 接受 Sentence 对象列表而非 SID 字符串
4. `match()` 增加 `valid_sids` 参数做 P3 SID 校验
5. GBNF `str` 规则允许转义序列
6. AI 独有"自动勾选"档追加"非 ambiguous"条件
7. `user_edited` 接口为 `{(行号, 字段)}` 集合

## 10. 关键设计文档索引

| 文档 | 路径 | 内容 |
|---|---|---|
| 设计规格 | `docs/superpowers/specs/2026-10-09-redesign-design.md` | v6 镜像布局、倒计时规格 §3.1、融合管线、配色、交互规范 |
| 实施计划 | `docs/superpowers/plans/2026-10-09-redesign-implementation.md` | 8 个 Task 拆解 + 验收清单 + 偏差登记 |
| AI 算法融合参考 | `docs/AI算法融合参考.md` | 四阶段管线完整设计（P0-P3），含评审意见与最终方案 |

## 11. 环境与依赖

- Python 3.13.3
- PyQt6 6.11.0（`pip install PyQt6`）
- llama.cpp b11505（预编译，`llama/llama-server.exe`）
- Qwen3-1.7B-Instruct Q8_0（`models/Qwen3-1.7B-Q8_0.gguf`，1.83GB）
- 无其他第三方依赖

## 12. 给接手设计者的建议

1. **布局可完全重做**：当前 v6 镜像布局在实际使用中点击响应不够直觉，可以重新设计
2. **ui.py 建议拆分**：MainWindow / ImportDialog / ReminderPopup / FloatBall 各拆独立文件
3. **数据层和逻辑层稳定**：storage.py / reminders.py / text_parser.py / countdown.py 经测试覆盖，不宜大改
4. **配色可调**：style.py 集中管理，改一个文件即可全局换色
5. **交互核心需求**：点任务→看详情、点日历→看当天安排、导入文本→自动解析，这三条是必须流畅的
