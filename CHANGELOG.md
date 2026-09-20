# 更新日志 / Changelog

本文件记录**麦麦恋人（MaiLover）本 fork（[cateyemizuki/Mai_love](https://github.com/cateyemizuki/Mai_love)）**的版本变更。
格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.1/)，版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

> **溯源**
>
> - **原作者 / 上游项目**：[octmicy/Mai_love](https://github.com/octmicy/Mai_love)（MIT，版权归原作者所有）。
> - **现维护者**：[cateye](https://github.com/cateyemizuki)，自 v2.4.1 起接手维护。
> - 插件 ID `maibot-community.mai-love` **保持不变**，其他插件依赖它调用公开 API。
> - 本 fork 的 2.3.0 – 2.4.0 改动已在 README「Fork 改动说明」中按版本记录；本文件自 v2.4.1 起接管版本变更记录。

## [2.4.2] - 2026-09-20

### 新增（Added）

- **「主动发言最小间隔」**（`schedule.min_trigger_interval_minutes`，0~1440 分钟，**默认 240**）：
  任意两次主动发言之间的**硬性间隔**，默认对**所有**主动触发生效（含早安/晚安）。
  新增 `schedule.min_interval_exempt_greetings`（默认 false）决定早晚安是否豁免该间隔。
  背景：此前想做"至少 4 小时才主动说一次"**做不到**——唯一的间隔旋钮
  `schedule.user_cooldown_minutes` 被校验器硬夹在 **0~60**（填 240 会被静默收敛成 60），
  且早安/晚安会无视它（`scheduler.py` 的 S级分支不检查冷却）；
  `time_windows.miss_trigger_hours_min/max` 则**只影响「想念」**这一种触发（且每天最多一次）。
- **「主动行为决策日志」+ `/mai_diag [天数]` 命令**（`decision_logger.py`，
  `[proactive_log]` 配置节：`enabled` / `record_skips` / `retention_days`）：
  每轮巡检写**恰好一条**判定结论——是否静默、距上次发言多久、最小间隔/冷却是否通过、
  概率掷点、当日预算余量、最终动作与原因。落盘
  `data/plugins/maibot-community.mai-love/proactive_logs/decisions_YYYY-MM-DD.jsonl`。
  背景：主动私聊**不调用插件的 LLM**，所以它不会出现在 LLM 调用日志里
  （这正是"触发过却没有日志"的原因）；宿主主日志里也只有一行 `B级触发: 日常巡检`，
  看不出被哪个旋钮挡住。
- 跳过原因按 `SKIP_PRIORITY` 只保留最相关的一条，避免"早安被最小间隔挡住"
  被后面"日常巡检掷点没中"覆盖，导致日志答非所问。

### 修复（Fixed）

- **静默时段会静默失效**：`_is_in_time_window` 解析失败时返回 False（= 不在窗口内），
  因此时间格式写错会让静默时段**失去作用且无任何提示**。现在：
  非法值记 warning（同一值只告警一次），并按 fail-safe 处理——
  静默时段**视为静默**（宁可不发）、早晚安窗口**视为不在窗口内**（不误发）。
  新增静态方法参数 `invalid_result`（keyword-only，保持既有调用与测试兼容）。
- **跨午夜丢失间隔约束**：`AffectionManager.reset_daily()` 原本会清空 `last_speak_time`，
  导致每天 0 点后"距上次发言"的约束消失（23:58 刚发过、00:08 又能发）。
  现在不再清空该字段；`today_speak_count` 与当日早晚安/想念标记仍照常重置。

### 变更（Changed）

- `_trigger_morning` / `_trigger_night` / `_trigger_missing` / `_trigger_activity` /
  `_trigger_daily` 现在返回 `bool`（是否成功入队），供决策日志如实记录
  `planner_trigger_failed`——此前 `_trigger_planner` 因 `stream_id` 缺失等原因
  静默返回 False 时，日志里只留下一行"已触发"，属于误导。
- `_is_in_time_window`、`Scheduler.__init__`（新增可选参数 `decision_logger`）签名扩展，
  既有调用方无需改动。
- `config.py`：`user_cooldown_minutes` 补上 `ge=0, le=60` 与描述里的上限说明；
  `miss_trigger_hours_min/max` 描述改为「**仅对「想念」生效**」并指向最小间隔配置；
  `check_interval_minutes` 描述与 README 表格里的默认值/取值对齐。
- README：新增「主动发言节奏：哪个旋钮管哪个触发」对照表、`/mai_diag` 命令、
  「主动行为日志」配置节、三条新 FAQ，并修正调度设置表格里过期的默认值。

### 兼容性说明

- ⚠️ **默认值会改变行为**：`min_trigger_interval_minutes` 默认 **240**（至少间隔 4 小时），比 v2.4.1 明显更安静。想保持 v2.4.1 的旧节奏请显式填 `0`。
- `config_version` 与 manifest 版本同步：**2.4.1 → 2.4.2**（宿主首次加载会按新默认值补齐字段，
  用户已有配置值保留）。
- 决策日志默认开启（只写日志，不参与任何判定）；不想留日志可关 `[proactive_log] enabled`。
- 许可证不变（MIT），上游版权声明原样保留。

## [2.4.1] - 2026-09-20

### 修复（Fixed）

- **planner 活动注入在带图上下文里被整体跳过**（`plugin.py` `on_planner_before_request`）：
  原实现里 payload 超过 **1MB** 就直接跳过注入，而宿主会把图片以 `image_base64` 内联进
  `items` 快照（`src/llm_models/request_snapshot.py:264-271`），**带图上下文的载荷常态就是
  2–12MB** —— 线上日志实测【当前日期】【当前状态】【好感度】注入几乎从未生效（30 次以上
  `planner payload 过大，跳过活动注入`）。现改为：
  - 阈值提升到 `_MAX_INJECT_PAYLOAD_BYTES = 8MB`（宿主单帧上限 16MB，留编码余量，
    见 `src/plugin_runtime/transport/base.py:18`）；
  - 超限时**按体积从大到小把图片 part 换成文本占位符**（`[图片已省略：为控制上下文体积]`）
    后照常注入——替换而不是删除 part，保证 item 仍有 part、宿主反序列化不会失败；
  - 无图片可裁（纯文本超限）时才回退为原来的跳过行为，并给出明确告警。

### 新增（Added）

- **主动发言回合的「回复目标约束」**（`plugin.py` `_build_proactive_target_rule`）：
  宿主 `maisaka.proactive.trigger` 能力只有 `stream_id / intent / reason / priority / metadata`
  （`src/plugin_runtime/capabilities/core.py:219-250`），**没有任何回复目标参数**——它只做
  "注入一条合成任务消息 + 强制触发一轮 + 投递 proactive 轮"，回复哪一条**完全由 planner
  自主决定**。而主动私聊那一轮**没有被回复的用户消息可锚**，于是目标落到 bot 自己上一条发言上
  （宿主对此是支持的：目标是 bot 自己时 `src/chat/replyer/maisaka_generator_base.py:182-189`
  会提示"补充说明你自己发送的消息"），群里看起来就是 bot 在引用自己说话。

  现在在最近 **90 秒**内有过 proactive trigger 时（复用插件既有的 `get_last_trigger_time()`
  惯例，不影响普通用户消息回合），向 planner 注入一条 system 消息：

  - 不要回复你自己发送的消息，也不要在发言里引用你自己的消息；
  - 优先回应对方最后一条发言；如果最后一条发言是你自己的（对方还没回你），就当作对方还没回，
    直接说新内容或起一个新话题；
  - `reply` 的 `msg_id` 只能选对方发送的消息。

> ⚠️ 这是**软约束**：宿主没有强制指定回复目标的参数，模型仍可能偶尔选中自己的消息。
> 若要真正做到"目标可控"，需要给 `maisaka.proactive.trigger` 增加可选 `reply_target_msg_id`，
> 由框架写进注入文本或作为 `reply` 工具默认参数——那属于改宿主核心，不在本 fork 范围内。

### 变更（Changed）

- `_manifest.json`：`urls.repository` 指向本 fork，`urls.homepage` 指向上游原项目，
  `urls.issues` 指向本 fork；`author` 保留原作者（标注"原作者"）；`description` 补充
  fork 与维护者说明；`version` 2.4.0 → 2.4.1。
- `README.md`：新增「作者与维护」章节（原作者 / 现维护者 / 本仓库 / 许可证），
  「Fork 改动说明」补充第 4 项（v2.4.1），原「本地修改」章节改为「v2.4.1 改动明细」。
- 新增本文件 `CHANGELOG.md`。

### 兼容性说明

- 不开启 `schedule.use_external_schedule` 时，行为与上游一致；本版本两处改动只影响
  "带图上下文的 planner 活动注入" 与 "主动发言回合的回复目标"，不改变任何配置项语义。
- `config_version` 与 manifest 版本同步：**2.4.0 → 2.4.1**（宿主首次加载会按新默认值补齐字段，
  用户已有配置值保留）。
- 许可证不变（MIT），上游版权声明 `Copyright (c) 2026 octmicy` 原样保留。

## [2.4.0] 及更早

见 README「Fork 改动说明」第 1–3 项（外部日程开关、规范与安全整改、恋人电脑联动 + LLM 调用日志）。
