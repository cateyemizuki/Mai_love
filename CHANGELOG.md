# 更新日志 / Changelog

本文件记录**麦麦恋人（MaiLover）本 fork（[cateyemizuki/Mai_love](https://github.com/cateyemizuki/Mai_love)）**的版本变更。
格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.1/)，版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

> **溯源**
>
> - **原作者 / 上游项目**：[octmicy/Mai_love](https://github.com/octmicy/Mai_love)（MIT，版权归原作者所有）。
> - **现维护者**：[cateye](https://github.com/cateyemizuki)，自 v2.4.1 起接手维护。
> - 插件 ID `maibot-community.mai-love` **保持不变**，其他插件依赖它调用公开 API。
> - 本 fork 的 2.3.0 – 2.4.0 改动已在 README「Fork 改动说明」中按版本记录；本文件自 v2.4.1 起接管版本变更记录。

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
