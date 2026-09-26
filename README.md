# 麦麦恋人（MaiLover）

**私聊专用虚拟恋人插件** — 基于 MaiBot Plugin SDK 2.5.4

> 麦麦有自己的生活 · 主动找你聊天 · 好感度调节温度

## 作者与维护

| | |
|---|---|
| **原作者** | octmicy — 上游项目 [octmicy/Mai_love](https://github.com/octmicy/Mai_love) |
| **现维护者** | cateye — [cateyemizuki](https://github.com/cateyemizuki) |
| **本仓库** | <https://github.com/cateyemizuki/Mai_love>（上游的 fork，自 **v2.4.1** 起由 cateye 维护） |
| **许可证** | MIT — 保留上游版权声明 `Copyright (c) 2026 octmicy` |

> 插件 ID `maibot-community.mai-love` **保持不变**（其他插件依赖它调用公开 API），
> 原作者信息保留在 `_manifest.json` 的 `author` 字段，维护者信息同时写在本 README 与仓库描述中。
> 本 fork 的逐版本改动见下文「Fork 改动说明」与 [CHANGELOG.md](CHANGELOG.md)。

> **版本要求**：MaiBot ≥ v1.2.3，maibot-plugin-sdk ≥ 2.6.0。
> 本插件依赖 `maisaka.proactive.trigger` 能力与 `maisaka.*` Hook，其 payload
> 契约以 1.2.3 为首个文档化基线，更低版本上这些功能会静默失效。

---

## Fork 改动说明

> 本仓库 fork 自 [octmicy/Mai_love](https://github.com/octmicy/Mai_love)（基于上游 v2.2.0），在其基础上做了以下改动：

### 1. 新增「使用外部日程」开关（`schedule.use_external_schedule`，默认关闭）

与「麦麦自主规划插件」（`xuqian13.autonomous-planning-plugin-v4`）联动，二选一接管日程来源：

- **开启后**：本插件不再自行生成日程（每日生成循环不启动，`generate_daily_schedule` 内部短路双保险），并**清空已生成的日程缓存**（`schedule_cache.json` 与 `.schedule_generated` 标记）；
- **日程改读自主规划插件**：巡检时通过跨插件 API `ctx.api.call("xuqian13.autonomous-planning-plugin-v4.get_current_activity")` 拉取当日日程快照，转换为 `{time, activity}` 节点后合并进本地缓存——早安/晚安、日程节点分享、"在干嘛"查询等原有逻辑无需任何改动即可复用；
- **数据格式兼容层**：自主规划插件是"时间窗口"制（`HH:MM-HH:MM`），本插件是"时间点"制（`HH:MM`），转换规则为窗口起点 → 节点时间、活动名 → 节点活动，跨夜活动（如 23:00-07:00 睡觉）天然正确；
- **优雅降级**：对方插件未安装/未启用/当日尚未生成日程时按"今日暂无日程"处理，不影响早安、晚安、想念等与日程无关的触发；拉取失败保留旧缓存待下次巡检重试（失败日志 30 分钟节流，不刷屏）；
- **无日程不注入**（v2.3.0）：对方无日程返回时（自主规划插件 v4.7 起无睡眠时段不生成日程、凌晨日切后当日日程尚未生成），planner 注入**跳过日程状态行**，不再出现"麦麦正在今天还没有安排"这类占位句；日期/好感度注入不受影响；
- **关闭开关**即恢复本插件自动生成（下次调度器启动时立即补生成当日日程）。

### 2. 其他改动

- 新增 `external_schedule.py`：外部日程源（API 调用、格式转换、TTL 节流）；
- `schedule_generator.py`：外部模式短路、缓存清理、`refresh_external_schedule` 合并刷新；
- `scheduler.py`：启动流程按开关分流（清空缓存/跳过生成循环），巡检 `_tick` 前刷新外部日程；
- `/mai_config` 与 `mai_lover_config` Tool 增加"日程来源"展示；`/mai_schedule` 空日程提示区分外部模式；
- 离线回归测试仅保留在本地开发环境（不随插件发布）；`config.toml` 与 `README.md` 同步更新；
- **想念机制改造**（v2.3.0）：触发时长改为可配置区间 `miss_trigger_hours_min/max`（每次巡检区间内随机取阈值，沉默越久越容易触发）；触发前可经 LLM 把关（`miss_llm_check_enabled`，驳回 30 分钟冷却）；把关提示词与触发提示词均可在配置中查看修改（见下文「想念触发提示词」）；无日程时不注入日程状态行。
- **恋人电脑联动 + LLM 调用日志**（v2.4.0）：新增 `[cateye]` 段——想念/早晚安触发时经 cateye.connect-hub 查看恋人电脑（截图 + 视觉模型转述，未连接则告知"电脑没开"）；新增 `[llm_log]` 段与 `/mai_llm_log` 命令——记录插件发起的全部模型请求回复（事件/时间/内容，默认保留 3 天，合并转发查看）；manifest 补声明 `send.forward` 能力。

### 3. v2.3.1 规范与安全整改

- **补声明 `api.call` 能力**：此前 manifest 未声明 `api.call`，开启外部日程后
  调用会被宿主以 `E_CAPABILITY_DENIED` 拒绝并静默吞掉，外部日程模式从未真正生效；
- **planner 活动注入修复**：宿主在 `maisaka.planner.before_request` 后只回读
  `items`/`messages`，此前写入 `extra_prompt` 会被忽略（那是 replyer Hook 的
  字段）——现改为向上下文追加一条 SystemMessageItem（快照投影）或 system
  消息（旧投影），日期/节假日/当前活动/好感度注入自此真正生效；
- **命令鉴权**：`/mai_*` 命令仅限白名单 `target_qq` 本人（及本机控制台）使用，
  `target_qq` 为默认值/无效值时一律拒绝（默认拒绝）；
- **命令正则兼容引用回复**：`^/mai_xxx` 的 `^` 锚点在"引用+命令"场景失配，
  改为 `(?<!\S)/mai_xxx` 负向前瞻 + 文末锚定写法；
- **`llm_model` 任务名修正**：`reply` 不是宿主合法任务名（应为 `replyer`），
  选项与旧配置值自动迁移，避免选中后所有生成静默降级；
- **依赖与版本**：manifest 声明 `httpx` 依赖；`config_version` 与 manifest
  版本同步（2.3.1）；`min_version` 抬高至 1.2.3；
- **调度器热更新竞态修复**：配置热更新时重建 Scheduler 实例，避免复用同一
  `stop_event` 造成新旧巡检循环并存。

### 4. v2.4.1 维护改动（cateye）

两处改动都针对**主动私聊**场景，源码里用 `[LOCAL-PATCH:cateye]` 注释标记（grep 该标记可定位全部改动行）：

- **planner 活动注入不再因载荷过大被整体跳过**：原实现 payload 超过 1MB 就直接 `跳过活动注入`，
  而宿主会把图片以 `image_base64` 内联进 `items` 快照，**带图上下文常态就是 2–12MB** ——
  线上日志实测【当前日期/当前状态/好感度】几乎从未进入 planner。现改为阈值 8MB（宿主单帧上限
  16MB），超限时按体积从大到小把图片 part 换成文本占位符后**照常注入**；无图片可裁才回退跳过。
- **主动发言回合注入「回复目标约束」**：宿主 `maisaka.proactive.trigger` 能力只有
  `stream_id / intent / reason / priority / metadata`，**没有任何回复目标参数**，回复对象完全由
  planner 自主决定；主动私聊那一轮没有用户消息可锚，目标就会落到 bot 自己上一条发言
  （群里看起来像 bot 在引用自己说话）。现在在最近 90 秒内有过主动触发时，向 planner 追加一条约束：
  不要回复/引用你自己发送的消息、优先回应对方最后一条发言、`reply` 的 `msg_id` 只能选对方的消息。
- 改动明细（含宿主源码位置与验证方式）见文末「[v2.4.1 改动明细](#v241-改动明细cateye)」。

### 5. v2.4.2 维护改动（cateye）

针对"配置了间隔却依然频繁主动发言""触发过却没有日志"两个真实困惑：

- **新增「主动发言最小间隔」**（`schedule.min_trigger_interval_minutes`，**默认 240 = 至少间隔 4 小时**，填 0 关闭）：
  对所有主动触发（**含早安/晚安**）生效的硬性间隔，0~1440 分钟。
  此前想做"至少 4 小时才主动说一次"是**做不到**的——唯一的间隔旋钮
  `user_cooldown_minutes` 被校验器硬夹在 **0~60**，早安晚安还会无视它；
  `miss_trigger_hours_min` 又只管「想念」这一种触发（且每天最多一次）。
  另加 `min_interval_exempt_greetings` 决定早晚安是否豁免该间隔。
- **新增「主动行为决策日志」+ `/mai_diag` 命令**：每轮巡检记录一条判定结论
  （是否静默、距上次发言多久、最小间隔/冷却是否通过、概率掷点、预算余量、最终动作与原因），
  跳过原因按优先级只保留最相关的那条（避免"早安被最小间隔挡住"被"日常巡检没掷中"覆盖）。
  落盘在 `data/plugins/maibot-community.mai-love/proactive_logs/`。
  **主动私聊本身不调用插件的 LLM**，所以它不会出现在 LLM 调用日志里——这是此前"触发过却没日志"的原因。
- **静默时段不再静默失效**：时间窗口配置写成非 `HH:MM` 格式时，原来会**静默地**当成"不在静默期"
  （等于静默失效且无任何提示）。现在记 warning，并 fail-safe：静默时段视为"在静默中"（宁可不发）、
  早晚安窗口视为"不在窗口内"（不误发）。
- **跨午夜不再丢间隔**：`reset_daily` 不再清空 `last_speak_time`（清空会让 23:58 刚发过、
  00:08 又能发）。
- **文档澄清**：「哪个旋钮管哪个触发」对照表见上文「[主动发言节奏](#主动发言节奏哪个旋钮管哪个触发)」。
- 改动明细见文末「[v2.4.2 改动明细](#v242-改动明细cateye)」。

### 6. v2.4.3 维护改动（cateye）

让决策日志**覆盖所有主动发言行为**，特别是**日程来自外部插件**的情况：

- **新增 `spoken`（确认发言）**：`maisaka.replyer.after_response` 在触发后 90 秒窗口内回执，
  记录"planner 确实生成并发出去了"，并标注是哪种触发。原来的 `trigger` 只代表"已入队"。
- **新增 `tool`（Tool 主动发消息）**：planner 通过 `mai_lover_send_message` 主动发消息也进日志。
- **外部日程拉取结果进日志**（`info`）：`fresh/cached/empty/error/exception` + 今日节点数。
  外部日程模式下本插件不生成日程，**拉不到节点就永远不会有「日程节点分享」**——
  以前这件事完全不可见。`fresh`/`cached` 归并为同一状态避免刷屏，失败每次都记。
- **`/mai_diag` 增加运行状态行**（巡检是否在跑 / 上次巡检时间 / stream_id 是否解析 /
  日程来源与今日节点数 / 外部拉取状态）；**日志为空时也带这行**，直接区分
  "巡检没启动（stream_id 没解析）"与"刚加载还没巡检过"。

> 起因：线上执行 `/mai_diag` 得到"最近 3 天没有主动行为决策记录"，却无法判断是刚加载、
> 还是巡检压根没跑。现在这条提示会直接给出运行状态。

> 上游原有无外部日程开关，所有改动向后兼容：不开启 `use_external_schedule` 时行为与上游完全一致。

### 7. v2.5.0 维护改动（cateye）

修掉"私聊恋人设定污染群聊"与"节假日判断从来没生效"两个线上问题：

- **注入按会话分档**（`plugin.py`）：「当前日期」对所有会话注入（中性事实）；
  「恋人当前状态 / 好感度」**默认只在目标用户私聊注入**。此前注入完全不看
  `session_id`，任何会话（含群聊）都会被塞进"【麦麦对用户的好感度】档位 2（热恋阶段）"，
  而文案又不带用户标识——线上日志实测模型因此在群里按"与恋人的热恋档位"对待所有群友。
- **群聊注入开关**（`injection.group_affection_enabled`，默认关闭）：打开后仅当上下文
  最后 `injection.group_recent_user_messages` 条（默认 15）**用户消息的发送者 QQ 号里
  出现目标用户**才注入，避免 bot 在群里只围着恋人转、不理其他人。
- **判定只看 QQ 号**：新增 `sender_identity.py` + 入站 Hook 记录
  「消息 ID → 发送者 QQ 号」与「会话 ID → 是否群聊」，请求前按 `msg_id` 反查发送者，
  昵称/群名片不参与判定（只用于文案展示），杜绝"某某的小号"这类形近名误命中。
- **注入文案标注用户**：写成「【麦麦对用户 小美（QQ 100000000）的好感度】…」，
  好感度属于谁一目了然。群聊档再附一句"恋人语气只在与 TA 直接互动时使用"的限定；
  **私聊档不加范围说明**——私聊会话本身就确定了对象、上下文里没有第三方，
  多写一句只是白烧 token。
- **节假日判断修复**（`holiday_service.py`）：旧实现读的是 `data["holiday"]["type"]`，
  而类型其实在 `data["type"]["type"]`——**永远取不到 → 每次都退回"按星期几判断"**，
  于是国庆节显示"工作日"、调休补班的周日显示"周末休息日"。现在改为拉取**整年放假安排表**
  （含调休补班日）并落盘缓存，支持主备两个数据源。

> 详细版见文末「[v2.5.0 改动明细](#v250-改动明细cateye)」。

### 8. v2.6.0 维护改动（cateye）

**主题：先看屏幕，再叫醒 planner；顺手把「想念永远不触发」放出来。**

- **屏幕感知从「拼提示词」改为「先进上下文，再唤醒 planner」**：触发时先取屏幕
  （截图 + 视觉转述），拼成旁白发布到新的 `screen_context` 缓存，**然后**才调
  `maisaka.proactive.trigger`——`reason` 里不再夹带电脑状态，planner 自然读到。
- **覆盖全部主动触发**：早安 / 晚安 / 想念 / 日程节点分享 / 日常巡检。
  此前只有早晚安与想念会看屏幕。
- **旁白以「尾随锚点」请求级注入**（`screen_context.py`）：不落宿主历史、不写消息库、
  不真发到平台。触发时锁定「上下文里最后一条真实聊天消息」当锚点，之后每轮
  planner / replyer 请求把旁白作为 **bot 身份**的条目插到锚点之后；锚点从该通道
  上下文消失就**只作废该通道**（两者上下文不一定相同），都作废后清缓存。
  只对**恋人私聊流**生效，群聊与其他会话一律不注入。
- **`miss_avoid_future_schedule`（默认关闭）**：此前"未来 2 小时内有日程节点就不打扰
  想念"是硬编码，外部日程模式下节点密集 → 这道闸门几乎恒为真，想念**永不触发**。
  现在默认放行，需要旧行为可手动打开；窗口长度由 `miss_future_schedule_hours` 控制。
- **想念的屏幕感知挪到 LLM 把关之后**，被驳回不再白截一张屏。
- **「电脑没开 / 没看清」保留但换通道**：三种看屏幕结果（看清楚 / 电脑没开 / 没看清）
  各有一个可配模板，都走同一条旁白通道；对应模板**留空 = 那种情况不注入**。
  `cateye_client` 的返回值从「字符串」改成 `ScreenPeek` 状态对象
  （`get_computer_context` → `peek_screen`，`TEXT_COMPUTER_*` 已删除）。

> 详细版见文末「[v2.6.0 改动明细](#v260-改动明细cateye)」。

---

## 简介

麦麦恋人是为 MaiBot 设计的私聊专用插件，模拟一个有独立人格、会主动找你聊天的"网恋对象"。

**和普通聊天机器人的区别**：麦麦有自己的日程生活（赖床、做饭、晒太阳、偶尔想你），由 MaiBot 的 planner 大脑自主决策什么时候主动找你说话、说什么。你问"在干嘛"时，她会根据当前正在做的事自然回答。

### 核心机制

| 机制 | 说明 |
|------|------|
| **独立日程生活** | 每天自动生成麦麦一天的活动安排（赖床/做饭/晒太阳/想你），不是用户的日程 |
| **planner 自主发言** | 麦麦的"大脑"（MaiBot planner）自主决定说不说、说什么，插件只负责"提醒大脑该想想了" |
| **好感度系统** | 3 档位（熟悉/亲密/热恋）影响语气，可通过指令调整 |
| **静默时段** | 睡觉时间不打扰，可配置静默起止时间 |

---

## 核心特性

- **白名单守卫** — 仅指定 QQ 号私聊生效
- **麦麦虚拟生活** — 每天凌晨结合人设 + 作息骨架 + LLM 生成当天的活动安排
- **planner 状态注入** — 每次 planner 思考时都能看到"麦麦现在在干嘛"，让回复自然带上当前状态
  （v2.5.0 起：日期全会话注入，**恋人上下文默认只注入目标私聊**，可在配置里放开到群聊）
- **主动聊天** — scheduler 定时提醒 planner"可以考虑说话了"，planner 自主决定发不发
- **早晚安仪式** — 早安/晚安时间窗触发 planner，麦麦主动跟你说早/晚安
- **想念机制** — 你太久没理她，麦麦会跑来说想你：触发时长是**可配置区间**
  （沉默越久越容易触发），触发前还可让 **LLM 以角色身份把关**，此刻开口
  不自然就驳回，不再"被代码推着硬接话题"（每天最多 1 次）
- **"在干嘛"查询** — 你问"在干嘛"时，planner 调用 Tool 查麦麦当前活动，自然回答
- **静默时段** — 配置睡觉时间，那段时间麦麦完全安静
- **好感度系统** — 3 档位影响语气温度（熟悉/亲密/热恋）
- **恋人电脑联动**（可选，需 cateye 插件）— **每次**主动找你说话前先看一眼你的电脑
  （早安 / 晚安 / 想念 / 日程节点 / 日常巡检都会看）：已连接则截图 + 视觉模型转述
  「TA 在干什么」，以麦麦自己的口吻写进上下文（`现在是X月X日 X:XX，你看了眼…的电脑屏幕，
  TA正在：…`），再唤醒 planner 决定说什么；电脑没开 / 没看清也各有一句旁白
  （沿用 v2.5.0 语义，可留空关掉）
- **LLM 调用日志** — 记录插件发起的所有模型请求回复（事件来源/时间/回复内容），
  默认保留 3 天，`/mai_llm_log` 合并转发查看
- **人设同步** — 自动读取 MaiBot 主程序的人格配置，日程生成符合麦麦性格

---

## 快速开始

### 1. 安装

将 `mai_lover` 目录放入 MaiBot 的 `plugins/` 目录：

```
MaiBot/
└── plugins/
    └── mai_lover/
```

### 2. 配置白名单

编辑 `config.toml` 或在 MaiBot WebUI 中修改，将 `target_qq` 改为你的 QQ 号：

```toml
[whitelist]
target_qq = 100000000  # ← 改成你的 QQ 号
```

### 3. 配置麦麦作息（可选）

编辑 `mai_template.json`，预设麦麦工作日和周末的作息骨架。插件每天凌晨会结合人设 + 骨架 → LLM 生成当日完整活动安排。

### 4. 启动

启动 MaiBot 后插件自动加载。首次启动会立即生成今日日程（不用等到第二天凌晨）。

---

## 交互方式

### 用户命令

> v2.3.1 起命令增加鉴权：仅白名单 `target_qq` 本人（及本机控制台）可使用，
> 其他用户触发会被拒绝并拦截。

| 命令 | 功能 |
|------|------|
| `/mai_status` | 查看麦麦状态（好感度/今日触发/日程摘要） |
| `/mai_schedule` | 查看麦麦今日完整活动安排 |
| `/mai_affection <0\|1\|2>` | 调整好感度档位 |
| `/mai_config` | 查看插件配置摘要 |
| `/mai_llm_log [天数]` | 查看插件发起的 LLM 调用日志（合并转发，每条请求一条消息） |
| `/mai_diag [天数]` | 查看**主动行为决策日志**：每轮巡检一条，写明"为什么发言/为什么没发言"（静默、最小间隔、冷却、概率、上限），并含**确认发言**、**Tool 主动发消息**与**外部日程拉取结果**；汇总行带运行状态（巡检在跑吗 / stream_id 解析了吗 / 日程来源与节点数） |
| `/mai_help` | 列出所有可用命令 |
| `/mai_test` | 发送测试消息验证发送通道 |

### LLM 工具（planner 可调用）

| Tool | 功能 |
|------|------|
| `mai_lover_current_activity` | 查询麦麦现在在干嘛（你问"在干嘛"时 planner 会调这个） |
| `mai_lover_status` | 查看麦麦状态 |
| `mai_lover_schedule` | 查看今日完整日程 |
| `mai_lover_send_message` | 主动发一条恋人消息 |
| `mai_lover_affection` | 调整好感度 |
| `mai_lover_config` | 查看配置 |

### 扩展 API（供其他插件调用）

| API | 功能 |
|-----|------|
| `get_current_activity` | 获取麦麦当前活动 |
| `get_schedule` | 获取今日完整日程 |
| `get_affection_level` | 获取好感度档位 |

---

## 配置说明

所有配置项均可在 `config.toml` 中修改，也可通过 MaiBot WebUI 直接调整。

### 插件设置
| 参数 | 默认值 | 说明 |
|------|--------|------|
| `plugin.enabled` | true | 插件总开关 |
| `plugin.llm_model` | planner | 生成日程用的模型 |

### 白名单
| 参数 | 默认值 | 说明 |
|------|--------|------|
| `whitelist.target_qq` | 123456789 | 绑定的 QQ 号 |

### 调度设置
| 参数 | 默认值 | 说明 |
|------|--------|------|
| `schedule.generate_hour` | 3 | 每天几点生成日程 (0-23) |
| `schedule.check_interval_minutes` | 10 | 巡检间隔（分钟） |
| `schedule.daily_max_speak` | 5 | 每日主动触发上限（含早晚安；0 = 完全静音） |
| `schedule.user_cooldown_minutes` | 30 | 用户发言后冷却（分钟，**上限 60**） |
| `schedule.min_trigger_interval_minutes` | **240** | **主动发言最小间隔（分钟，0~1440）**：任意两次主动发言之间的硬性间隔，默认对所有主动触发生效。默认 240 = 至少间隔 4 小时；填 `0` 关闭 |
| `schedule.min_interval_exempt_greetings` | false | 早安/晚安是否豁免上面的最小间隔 |
| `schedule.proactive_trigger_enabled` | true | 麦麦会不会主动找你 |
| `schedule.use_external_schedule` | false | 使用外部日程：读取自主规划插件，本插件清空缓存且不再生成 |

### 主动发言节奏：哪个旋钮管哪个触发

最容易踩的坑是**把「想念触发下限」当成主动发言的整体间隔**——它只管「想念」这一种触发。
实际生效关系如下：

| 触发 | 什么时候触发 | 受哪些旋钮限制 |
|------|--------------|----------------|
| **S级 早安 / 晚安** | 进入 `morning_*` / `night_*` 窗口且当天没发过 | 静默时段、`min_trigger_interval_minutes`（可用 `min_interval_exempt_greetings` 豁免）、`daily_max_speak`。**无视「用户冷却」与概率** |
| **A级 想念** | **用户**沉默 > 区间内随机阈值（`miss_trigger_hours_min`~`max`，每天最多 1 次） | 静默、最小间隔、用户冷却、概率 `miss_speak_rate`、上限、未来有日程则不打扰（`miss_avoid_future_schedule`，**v2.6.0 起默认关闭**）、LLM 把关 |
| **B级 日程节点分享** | 当前时间命中日程节点 | 静默、最小间隔、用户冷却、概率 `activity_trigger_rate`、上限 |
| **B级 日常巡检** | 每轮巡检掷点 | 静默、最小间隔、用户冷却、概率 `default_speak_rate`、上限 |

结论（想调"多久主动找我一次"）：

- **整体间隔** → `schedule.min_trigger_interval_minutes`（默认 240 分钟 = 4 小时，对所有触发生效，含早晚安）。
- **你刚说完话后的安静期** → 改 `schedule.user_cooldown_minutes`（**最大 60 分钟**，早安晚安不受它管）。
- **想念的沉默门槛** → 改 `time_windows.miss_trigger_hours_min/max`（**只影响想念，且每天最多一次**）。
- **每天最多几次** → 改 `schedule.daily_max_speak`。
- **完全不想被打扰的时段** → 改 `time_windows.silence_start/silence_end`。

判定过程可查：`/mai_diag`（每轮巡检一条，写明被哪个旋钮挡住）。

### 概率设置
| 参数 | 默认值 | 说明 |
|------|--------|------|
| `probability.default_speak_rate` | 0.6 | 日常主动找你说话的概率 |
| `probability.miss_speak_rate` | 0.5 | 想念触发概率 |
| `probability.activity_trigger_rate` | 0.6 | 日程节点到点分享的概率 |

### 时间窗口
| 参数 | 默认值 | 说明 |
|------|--------|------|
| `time_windows.morning_start` | 06:00 | 早安窗口开始 |
| `time_windows.morning_end` | 09:00 | 早安窗口结束 |
| `time_windows.night_start` | 22:00 | 晚安窗口开始 |
| `time_windows.night_end` | 23:59 | 晚安窗口结束 |
| `time_windows.miss_trigger_hours_min` | 4.0 | 【**仅对想念生效**】想念触发区间下限（小时）：**用户**沉默不足绝不触发。它不是主动发言的整体间隔——整体间隔看 `schedule.min_trigger_interval_minutes` |
| `time_windows.miss_trigger_hours_max` | 8.0 | 【仅对想念生效】想念触发区间上限（小时）：沉默超过后时长条件必满足 |
| `time_windows.miss_avoid_future_schedule` | false | 【**仅对想念生效**】开启后：未来一段时间内还有日程节点就先不打扰。**v2.6.0 起默认关闭**——此前这是硬编码的 2 小时窗口，外部日程模式下会让想念永不触发 |
| `time_windows.miss_future_schedule_hours` | 2.0 | 【**仅对想念生效**】上面那个开关的窗口长度（小时），仅开关打开时生效 |
| `time_windows.miss_llm_check_enabled` | true | 想念触发前 LLM 把关（不自然则驳回，30 分钟后重试） |
| `time_windows.miss_reason_prompt` | （见下） | 想念触发时传给 planner 的提示词 |
| `time_windows.miss_confirm_prompt` | （见下） | LLM 把关提示词模板（Y 放行 / N 驳回） |
| `time_windows.silence_start` | 00:00 | 静默时段开始（睡觉不打扰） |
| `time_windows.silence_end` | 08:00 | 静默时段结束 |

### 想念触发提示词

想念机制涉及两条提示词，均可在 WebUI / `config.toml` 中查看和修改：

**1. 把关提示词**（`miss_confirm_prompt`，触发前）：沉默时长满足区间条件且通过
概率掷点后，先用这条提示词让 LLM 扮演麦麦判断"此刻主动说想你了是否自然"，
回复 `Y` 才真正触发，`N`（或无法解析）则本轮驳回，30 分钟内不再重复判断：

```text
你是虚拟恋人"{lover_name}"（人设：{personality}）。现在是 {current_time}，你上次收到用户的消息已经是 {hours} 小时前。{activity_context}
请站在"{lover_name}"的角度判断：此刻主动发一条"想你了 / 关心近况"的消息是否自然、是否体贴？
- 深夜TA可能睡了、TA可能在忙、或你觉得突兀 → 回复 N
- 你确实想TA了、此刻开口很自然 → 回复 Y
只回复一个大写字母：Y 或 N。
```

**2. 触发提示词**（`miss_reason_prompt`，触发后）：把关通过、真正触发时传给
planner 的 `reason`，planner 据此自主决定说什么（不再是被代码写死的
"用户很久没理你了"）：

```text
你已经有 {hours} 个小时没收到用户的消息了，你有点想TA了。可以主动开口问问近况、表达一下想念；但不必强行找话题，如果觉得此刻开口不自然，平淡地打个招呼也可以。
```

可用占位符：`{lover_name}` 恋人名、`{hours}` 沉默小时数、`{personality}` 人设、
`{current_time}` 当前时间、`{activity_context}` 当前活动上下文（无日程时为
"你现在没有安排中的活动。"）。

### 好感度
| 参数 | 默认值 | 说明 |
|------|--------|------|
| `affection.current_level` | 0 | 0=熟悉 / 1=亲密 / 2=热恋 |

### 恋人电脑（cateye 联动）
| 参数 | 默认值 | 说明 |
|------|--------|------|
| `cateye.enabled` | false | 主动触发时查看恋人电脑（需安装 cateye.connect-hub 插件） |
| `cateye.screenshot_blur` | 0 | 截图模糊半径（0-64），0=清晰 |
| `cateye.vlm_task` | vlm | 截图理解使用的视觉模型任务名 |
| `cateye.timeout_seconds` | 20 | 查看电脑的整体超时（秒），超时不阻塞触发 |
| `cateye.describe_prompt` | （见 config.toml） | 截图的视觉理解提示词 |
| `cateye.narration_template` | （见下） | 看清楚时写进上下文的旁白模板 |
| `cateye.context_ttl_minutes` | 90 | 旁白在上下文里的存活上限（分钟），0=不限 |
| `cateye.offline_narration_template` | （见下） | 电脑没开时的旁白模板，**留空 = 这种情况不注入** |
| `cateye.failed_narration_template` | （见下） | 截图/理解失败（没看清）时的旁白模板，**留空 = 这种情况不注入** |

**工作方式（v2.6.0 起）**：**所有**主动触发在叫醒 planner **之前**先取一次屏幕——
查 `cateye.connect-hub.status`，已连接则截图交给视觉模型转成一句话描述，
按对应模板拼成旁白；取到旁白后才 `proactive.trigger`，`reason` 里不再夹带电脑状态。

三种结果分别走三个模板（占位符 `{date}` 年月日、`{time}` 时分、`{user_name}` 恋人显示名、
`{description}` 视觉转述）：

| 看的结果 | 配置项 | 默认文案 |
|---|---|---|
| 看清楚 | `narration_template` | `现在是{date} {time}，你看了眼{user_name}的电脑屏幕，TA正在：{description}` |
| 电脑没开 | `offline_narration_template` | `现在是{date} {time}，你想看一眼{user_name}的电脑屏幕，但TA的电脑没开` |
| 没看清 | `failed_narration_template` | `现在是{date} {time}，你看了眼{user_name}的电脑屏幕，但没看清TA在干什么` |

「电脑没开 / 没看清」是 v2.5.0 就有的语义，v2.6.0 只是把它从「拼进 planner 提示词」
改成「走同一条旁白通道」——麦麦照样知道"这次没看到"，但不再有额外提示词。
**留空对应模板 = 那种情况不注入**（想只保留"看清楚了"就清空后两项）。
功能关闭、整体超时仍按不注入处理（与 v2.5.0 一致）。

**旁白是怎么进上下文的（尾随锚点）**：不往提示词里塞，也不落进宿主历史，而是
**请求级注入**——触发时先锁定「触发那一刻上下文里最后一条真实聊天消息」当锚点，
之后每一轮 planner / replyer 请求都把旁白作为一条 **bot 身份**的条目插到锚点**之后**；
某一轮该通道的上下文里锚点消失了（一般是超出上下文条数），就**只作废该通道**
（planner 与 replyer 的上下文不一定相同，分开识别），两者都作废后整条缓存清除。
另受 `context_ttl_minutes` 兜底。**只对恋人私聊流注入**，群聊与其他会话一律不碰。

> 「真实聊天消息」= 用户或 bot 真正发过的消息。会跳过宿主每轮现造的**合成 user 条目**
> （当前时间、planner 最终提醒、replyer 回复要求——它们没有 `<message msg_id="…">` 前缀、
> `item_id` 每轮都变，拿它们当锚点旁白只会注入一轮）、宿主的
> `<plugin_proactive_task>` 插件块、本插件自己注入的旁白，以及 system / 工具 / 推理 /
> 参考消息条目。

### LLM 调用日志
| 参数 | 默认值 | 说明 |
|------|--------|------|
| `llm_log.enabled` | true | 记录插件发起的模型请求回复（事件/时间/内容） |
| `llm_log.retention_days` | 3 | 日志保留天数，过期自动清理 |

事件来源包括：`schedule_generation`（日程生成）、`miss_confirm`（想念把关，
可看到每次驳回的原始回复）、`tool_send_message`（Tool 生成消息）、
`cateye_screen_describe`（屏幕理解）。用 `/mai_llm_log [天数]` 通过合并转发查看，
每次请求一条消息（标注时间、事件、模型与成功状态）。

### 主动行为日志（v2.4.2）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `proactive_log.enabled` | true | 记录每轮巡检的判定结论（静默/最小间隔/冷却/概率/上限/最终动作） |
| `proactive_log.record_skips` | true | 是否连"这一轮没发言"也记（关掉就只能看到发言记录，查不出"为什么没发"） |
| `proactive_log.retention_days` | 3 | 日志保留天数，过期自动清理 |

**为什么需要它**：主动私聊本身**不调用插件的 LLM**——插件只是把 `intent`/`reason`
文本交给宿主 `maisaka.proactive.trigger`，真正的 planner/replyer 调用由宿主发起。
所以「今天主动找过我」这件事**不会出现在 [LLM 调用日志] 里**，只会出现在本日志与宿主主日志中。
`/mai_diag [天数]` 会合并转发：首条是汇总（触发/跳过次数、跳过原因分布、当前节奏配置），
其后逐条列出每轮巡检的判定与依据。落盘位置：
`data/plugins/maibot-community.mai-love/proactive_logs/decisions_YYYY-MM-DD.jsonl`。
日志里会出现四类记录（v2.4.3 起覆盖全部主动发言行为）：

| 类型 | 含义 |
|---|---|
| ✅ `trigger` | 插件发起了一次主动触发（已入队，等 planner 决定说什么） |
| 🗣️ `spoken` | planner **确认生成并发送**了回复（触发后 90 秒内的回执），并标注触发类型 |
| ⏭️ `skip` | 这一轮没触发，附原因（静默 / 最小间隔 / 冷却 / 概率 / 额度 …） |
| ℹ️ `info` | 运行信息，目前是外部日程拉取结果（`external_schedule_*`，含今日节点数） |

> **外部日程模式**（`schedule.use_external_schedule = true`）下本插件不生成日程，
> 「日程节点分享」完全取决于能否从自主规划插件读到节点。拉取结果会以 `info` 记录
> （`fresh` / `cached` / `empty` / `error` / `exception`），一次都读不到时可直接看到原因。
> 汇总行还会给出运行状态：巡检是否在跑、上次巡检时间、`stream_id` 是否解析、
> 日程来源与今日节点数——**日志为空时也会附带这一行**，用来区分"没启动"与"刚启动"。

### 上下文注入（v2.5.0）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `injection.group_affection_enabled` | false | 群聊是否也注入「恋人当前状态 / 好感度」。默认**只在目标用户私聊**注入 |
| `injection.group_recent_user_messages` | 15 | 群聊注入的回看条数 X：上下文最后 X 条用户消息的**发送者 QQ 号**里出现目标用户才注入 |

**注入分两档**（`maisaka.planner.before_request`）：

1. **【当前日期】今天是 …（星期X），工作日/国庆节假期/调休工作日（国庆节后补班）**
   —— 所有会话都注入（中性事实，帮助模型正确理解"今天上不上班"）；
2. **【麦麦当前状态】现在 16:51，麦麦正在采购日用品。**
   **【麦麦对用户 小美（QQ 100000000）的好感度】档位 2（热恋阶段…）**
   —— 默认只在 `target_qq` 的私聊里注入，并标注好感度属于哪一位用户；
   群聊档会再加一句"恋人语气只对 TA 生效"的限定，**私聊档不加**（会话本身已确定对象）。

**为什么默认不给群聊**：注入不看会话时，群里每个人都会看到"麦麦对用户的好感度：热恋"，
模型会把恋人语气用到所有群友身上（实测会让 bot 在群里只围着恋人转）。打开
`group_affection_enabled` 后也不是无条件注入，而是要满足"最近 X 条用户消息里出现过 TA"——
即恋人刚说过话的那几轮才带上恋人上下文，之后自动回落，**不会让 bot 一直只在意恋人**。

**判定依据：只看 QQ 号**。宿主写进上下文的真实消息只有 `msg_id` 与显示名
（`user="昵称"` / `group_card="群名片"`），**没有 QQ 号**，而名字谁都能改（还会出现
「某某的小号」这种形近名），按名字判定必然误命中。所以插件走的是
[cateye_admin_identity](https://github.com/cateyemizuki) 那套已验证的机制：

1. `chat.receive.before_process`（入站，所有消息都经过）记录两份缓存：
   「消息 ID → 发送者 QQ 号」与「会话 ID → 是否群聊」；
2. planner 请求前，从条目文本里取 `msg_id` 反查发送者 QQ，**按 QQ 号判定**；
3. 反查不到（插件启动前就在上下文里的历史消息、缓存淘汰）或会话类型未知时
   **不注入** —— 宁可漏注入，也绝不靠名字兜底。

---

## 自定义麦麦作息

编辑 `mai_template.json`，预设麦麦工作日和周末的作息骨架：

```json
{
    "workday": [
        {"time": "08:30", "activity": "赖床中，闹钟响了还在赖"},
        {"time": "12:00", "activity": "做午饭，顺便想想下午干嘛"},
        {"time": "16:00", "activity": "想你了，等你消息ing"}
    ],
    "weekend": [
        {"time": "10:00", "activity": "周末赖床，阳光好舒服不想起"}
    ]
}
```

**节点字段**：
- `time` — 时间（HH:MM）
- `activity` — 麦麦在这个时间正在做什么（自然口语化描述）

每天凌晨插件会读取骨架 + 主程序人设 → LLM 生成完整日程（微调时间 + 加随机活动）→ 缓存到 `schedule_cache.json`。

### 使用外部日程（可选）

如果同时安装了「麦麦自主规划插件」（`xuqian13.autonomous-planning-plugin-v4`），可以把日程来源切换过去：

- `schedule.use_external_schedule = true` 后，本插件**清空已生成的日程缓存**，且**不再生成日程**；
- 巡检时通过插件 API 读取自主规划插件的当日日程（时间窗口起点 → 节点时间，活动名 → 节点活动），合并进本地缓存供早安晚安/日程节点等逻辑使用；
- 自主规划插件未安装、未启用或当日尚未生成日程时，本插件按"今日暂无日程"处理，不影响早安/晚安/想念等与日程无关的触发；
- 关闭该开关后恢复本插件自动生成（下次调度器启动时会立即补生成当日日程）。

---

## 工作原理

```
用户问"在干嘛" → MaiBot planner → 调 mai_lover_current_activity Tool
                                    → 返回"阳台晒太阳"
                                    → planner 自然回复"在阳台晒太阳呢~"

后台 scheduler → 定时巡检 → 检查静默/早安/晚安/想念/日程节点
              → 触发 ctx.maisaka.proactive.trigger(intent, reason)
              → planner 收到触发 → 思考要不要说话
              → planner.before_request Hook 注入"麦麦正在XX"
              → planner 自主决策发什么 → 发送消息

每天凌晨 → 读取人设 + mai_template.json + 节假日
        → LLM 生成麦麦今日活动 → 缓存
```

所有与主程序的通信均通过 **MaiBot Plugin SDK**（`self.ctx.*`），不直接访问主程序内部模块。

---

## 文件结构

```
mai_lover/
├── plugin.py              # 插件入口（生命周期/Hook/Tool/Command/API）
├── config.py              # WebUI 配置模型
├── _manifest.json         # 插件清单
├── config.toml            # 用户配置
├── constants.py           # 常量（后缀池/Prompt 模板）
├── affection_manager.py   # 好感度管理
├── memory_manager.py      # 记忆管理（预留）
├── llm_service.py         # LLM 调用封装
├── llm_logger.py          # LLM 调用日志（v2.4.0）
├── decision_logger.py     # 主动行为决策日志（v2.4.2）
├── message_service.py     # 消息发送 + 情绪后缀
├── holiday_service.py     # 节假日服务（整年安排表 + 调休补班 + 磁盘缓存，v2.5.0 重写）
├── sender_identity.py     # 发送者身份缓存（消息 ID→QQ 号 / 会话类型，v2.5.0）
├── schedule_generator.py  # 日程生成 + 活动查询
├── scheduler.py           # 调度引擎（触发 planner）
├── mai_template.json      # 麦麦作息骨架
├── data/                  # 运行时数据目录（好感度/日程缓存，自动生成）
└── README.md              # 本文件
```

> 数据目录（`ctx.paths.data_dir`，即 `data/plugins/maibot-community.mai-love/`）下会生成：
> `affection_memory.json`（好感度与当日计数）、`schedule_cache.json`（日程缓存）、
> `llm_logs/`（LLM 调用日志）、`proactive_logs/`（主动行为决策日志）、
> `holiday_cache.json`（整年放假安排表缓存，v2.5.0；拉不到网络时也能给出正确的调休信息）。

---

## 扩展开发

### 调用 mai_lover 的 API

其他插件可以通过 `ctx.api` 调用 mai_lover 暴露的 API：

```python
# 获取麦麦当前活动
activity = await ctx.api.call("maibot-community.mai-lover.get_current_activity")

# 获取今日日程
schedule = await ctx.api.call("maibot-community.mai-lover.get_schedule")

# 获取好感度
level = await ctx.api.call("maibot-community.mai-lover.get_affection_level")
```

### 自定义麦麦作息

编辑 `mai_template.json` 添加你想要的麦麦活动节点。活动描述越生动，planner 生成的回复越自然。

### 自定义好感度后缀

编辑 `constants.py` 的 `AFFECTION_SUFFIXES` 和 `BRACKET_THEATERS`，调整各档位的语气词。

---

## 常见问题

**Q: 为什么麦麦不主动找我？**
A: 检查：① `target_qq` 是否正确；② 是否在静默时段；③ `daily_max_speak` 是否用完；④ `proactive_trigger_enabled` 是否开启；⑤ 是否在冷却期内。

**Q: 麦麦会半夜发消息吵醒我吗？**
A: 不会。默认静默时段 00:00~08:00，这段时间麦麦完全安静。可在 `config.toml` 的 `time_windows.silence_start/silence_end` 修改。

**Q: 如何让麦麦更黏人？**
A: 调高 `probability.default_speak_rate` 和 `activity_trigger_rate`，降低 `schedule.check_interval_minutes`，增加 `schedule.daily_max_speak`。

**Q: 麦麦的人设从哪来？**
A: 自动读取 MaiBot 主程序的 `personality.personality` 配置。在 MaiBot WebUI 的"人格"设置里改，麦麦的日程生成会自动适配。

**Q: "在干嘛"是怎么回复的？**
A: 你问"在干嘛"时，MaiBot 的 planner 会自主决定调用 `mai_lover_current_activity` Tool 查询麦麦当前活动，然后基于结果自然回复。不是固定模板。

**Q: 麦麦怎么知道我在电脑上干什么？**
A: 需要 `cateye.enabled = true` 且同时安装「cateye 统一连接插件」（cateye.connect-hub）并在你的电脑上跑起它的客户端。满足条件后，想念/早晚安触发时会自动截图并用视觉模型转述你在干什么；电脑没开或未连接时麦麦只会知道"你的电脑没开"。

**Q: 为什么想看 LLM 日志却提示未启用？**
A: 检查 `[llm_log] enabled` 是否为 true；日志只记录插件自己发起的调用（日程生成/想念把关/Tool 消息/屏幕理解），planner 日常聊天回复由宿主产生，不在记录范围。

**Q: 我明明配置了"至少 4 小时"，为什么麦麦还是每隔半小时就主动找我？**
A: 因为那个 4 小时是 `time_windows.miss_trigger_hours_min`，它**只管「想念」这一种触发**
（条件是"**你**沉默 4~8 小时"，且每天最多一次），不是主动发言的整体间隔。
真正决定整体节奏的是 `schedule.user_cooldown_minutes`（默认 30 分钟，**上限 60**）与
`schedule.daily_max_speak`，而早安/晚安连冷却都会无视。
想让"至少 4 小时才主动说一次"，v2.4.2 起直接填
`schedule.min_trigger_interval_minutes`（**v2.4.2 起默认就是 240**）；对照表见「主动发言节奏」一节。

**Q: 今天麦麦主动找过我了，为什么 LLM 日志里没有？**
A: 正常。主动私聊**不调用插件的 LLM**——插件只把 `intent`/`reason` 交给宿主
`maisaka.proactive.trigger`，真正的 planner/replyer 由宿主发起、记在宿主自己的日志里。
主动行为本身请用 `/mai_diag` 查看（v2.4.2 起）。

**Q: 怎么查"这一轮为什么没发"？**
A: `/mai_diag [天数]`：每轮巡检一条，写明被哪个旋钮挡住（静默 / 最小间隔 / 冷却 / 概率 / 上限 /
想念时长不满足 / LLM 把关驳回等），以及当时的间隔与预算数值。
若只想知道"发过几次"，看首条汇总即可。
**Q: `/mai_diag` 说"没有记录"，是坏了吗？**
A: 通常是刚加载（第一轮巡检立刻跑，之后每 10 分钟一轮）。现在这条提示会**附带运行状态**：
「巡检=未运行」说明 `stream_id` 没解析出来（检查 `target_qq` 是否填对、适配器是否连上、
与目标用户的私聊流是否存在），此时主动发言整条链路都没跑；
「巡检=运行中」+「上次巡检=…」则说明日志在写，等下一轮再看即可。

**Q: 开了外部日程后，麦麦从来不分享日程节点？**
A: 外部日程模式下本插件不生成日程，节点完全来自「自主规划插件」的 API。
用 `/mai_diag` 看 `external_schedule_*` 记录：`empty` = 对方今日没有日程（或该时段无安排）；
`error` / `exception` = 对方插件未安装/未启用/API 报错（`detail` 里有原因）。
正常时汇总行会显示「日程来源=外部日程（今日节点 N 个）」。

**Q: 群聊里为什么看不到"麦麦对用户的好感度"了？（v2.5.0）**
A: 这是有意改成默认的。以前注入不看会话，群里每个人都会看到"好感度：热恋"，
模型会把恋人语气用到所有群友身上（实测会让 bot 在群里只围着恋人转、忽略其他人）。
现在【当前日期】仍然对所有会话注入，但【当前状态 / 好感度】默认只在 `target_qq` 的私聊里注入。
确实需要群聊也带上，就在 WebUI「上下文注入」里打开「群聊也注入恋人上下文」——
打开后也不是无条件注入：只有上下文**最后 15 条用户消息的发送者 QQ 号里出现 TA**
（按 QQ 号判定，与昵称/群名片无关）才注入，恋人刚说完话那几轮带上、之后自动回落。

**Q: 群聊注入会不会被"某某的小号"这种形近昵称骗到？**
A: 不会。判定只看 QQ 号：插件在入站链路上记录「消息 ID → 发送者 QQ 号」，
请求前用上下文里的 `msg_id` 反查，昵称与群名片完全不参与判定（只用于注入文案的展示）。
代价是**插件启动前就已在上下文里的历史消息**（以及超出缓存 TTL/容量的消息）反查不到，
这些消息不会被算作"恋人在场"——宁可漏注入，也不误判。
会话类型（群聊/私聊）同样由入站消息记录，类型未知时不注入群聊档。

**Q: 节假日/调休判断准吗？"今天到底上不上班"为什么有时显示老黄历？**
A: v2.5.0 起会联网拉取**整年放假安排表**（含调休补班日）并缓存到
`data/plugins/maibot-community.mai-love/holiday_cache.json`，一次请求覆盖一整年：
国庆、春节等假期会显示成「国庆节假期」，调休补班的周末会显示成
「调休工作日（国庆节后补班）」而不是"周末休息日"。
两点说明：① 网络完全不可用时仍会退回"按星期几判断"（此时调休信息会不准）；
② **次年的安排通常要到 11 月底才公布**，公布前查次年 1 月的日期会按普通工作日/周末回答，
缓存每 3 天自动刷新，公布后会自动纠正。

---

## v2.4.1 改动明细（cateye）

> 本节是上文「Fork 改动说明」第 4 项的详细版。v2.4.1 的 2 处改动在源码里全部用
> `[LOCAL-PATCH:cateye]` 注释标记（在 `plugin.py` 里 grep 该标记即可定位全部改动行）。
> **同步上游新版本时，记得把这两处改动重新应用一次**（上游更新后不会自动带上）。

### 1. planner 活动注入不再因 payload 过大被整体跳过

**问题**：原实现里 payload 超过 **1MB** 就直接 `跳过活动注入`。而宿主会把图片以
`image_base64` 内联进 `items` 快照（`src/llm_models/request_snapshot.py:264-271`），
所以**只要上下文里有图，payload 常态就是 2–12MB** → 线上日志实测这条注入几乎从未生效，
【当前日期】【当前状态】【好感度】全都没进 planner。

**改法**：阈值改为 `_MAX_INJECT_PAYLOAD_BYTES = 8MB`（宿主单帧上限 16MB，
见 `src/plugin_runtime/transport/base.py:18`）；超限时**按体积从大到小把图片 part 换成文本占位符**
（`[图片已省略：为控制上下文体积]`）后照常注入——替换而不是删除 part，保证 item 仍有 part、
宿主反序列化不会失败。无图片可裁时才回退为跳过。

### 2. 主动发言回合注入「回复目标约束」

**问题**：宿主 `maisaka.proactive.trigger` 能力只有
`stream_id / intent / reason / priority / metadata`（`src/plugin_runtime/capabilities/core.py:219-250`），
**没有任何回复目标参数**——它只做"注入一条合成任务消息 + `_arm_forced_turn_state` + 投递 proactive 轮"，
回复哪一条**完全由 planner 自主决定**。而主动私聊那一轮**没有被回复的用户消息可锚**，
于是目标就落到 bot 自己上一条发言上（宿主对此是支持的：目标是 bot 自己时，
`src/chat/replyer/maisaka_generator_base.py:182-189` 会提示"补充说明你自己发送的消息"），
群里看起来就是 bot 在引用自己说话。

**改法**：在 `maisaka.planner.before_request` 的注入文本末尾追加一段约束（仅在最近
`_PROACTIVE_RULE_WINDOW_SECONDS = 90` 秒内有过 proactive trigger 时注入，不影响普通用户消息回合）：

```
【本轮是主动发言】现在是你在主动找对方说话（不是对方来找你）：
- 不要回复你自己发送的消息，也不要在发言里引用你自己的消息；
- 优先回应对方最后一条发言；如果最后一条发言是你自己的（对方还没回你），就当作对方还没回，直接说新内容或起一个新话题；
- reply 的 msg_id 只能选对方发送的消息。
```

> 这是**软约束**（宿主没有强制目标的参数）。要真正"目标可控"，需要给
> `maisaka.proactive.trigger` 加一个可选 `reply_target_msg_id` 并让框架写进注入文本或作为
> `reply` 工具默认参数——那属于改宿主核心。

---

## v2.4.2 改动明细（cateye）

本节是上文「Fork 改动说明」第 5 项的详细版（含源码位置与验证方式）。

### 1. 主动发言最小间隔（`schedule.min_trigger_interval_minutes`）

**为什么需要**：v2.4.2 之前，想把主动发言拉成"至少 4 小时一次"是**做不到**的——

| 旋钮 | 真实作用域 | 上限 |
|---|---|---|
| `schedule.user_cooldown_minutes` | 只压住 B级（日程节点/日常巡检），早晚安无视 | 校验器 `_normalize_cooldown` 硬夹 **0~60**，填 240 会被静默收敛成 60 |
| `time_windows.miss_trigger_hours_min/max` | 只管 A级「想念」（且每天最多 1 次） | — |
| `schedule.daily_max_speak` | 管每天总量，不管间隔 | — |

**改动**：`config.py` 新增 `min_trigger_interval_minutes`（0~1440，**默认 240**）与
`min_interval_exempt_greetings`（早晚安是否豁免）；`scheduler.py` 的 `_tick` 在静默检查之后、
所有触发之前统一计算 `interval_blocked`（基准取 `affection.last_speak_time()`，
由每次成功触发经由 `increment_speak()` 刷新），S级/A级/B级 各分支分别按是否豁免判定。

### 2. 主动行为决策日志 + `/mai_diag`

**为什么需要**：主动私聊不调用插件的 LLM（只把 `intent`/`reason` 交给宿主
`maisaka.proactive.trigger`），所以"今天主动找过我"永远不会出现在 LLM 调用日志里；
而宿主主日志里也只有孤零零一行 `B级触发: 日常巡检`，看不出是被概率放过还是冷却已过。

**改动**：新增 `decision_logger.py`（`ProactiveDecisionLogger`，对齐 `llm_logger.py` 的
按天 JSONL + 过期清理约定），落盘 `proactive_logs/decisions_YYYY-MM-DD.jsonl`；
`Scheduler._tick` 每轮写**恰好一条**（`mark_trigger` / `mark_skip`），
新增 `plugin.py` 的 `/mai_diag [天数]` 命令（合并转发，含汇总行）；
`config.py` 新增 `[proactive_log]` 节（`enabled` / `record_skips` / `retention_days`）。

**一个设计细节**：一轮巡检里可能有多个候选触发都被挡住，若让后写的覆盖先写的，
就会出现"你问早安为什么没发、日志却报日常巡检掷点没中"。因此
`SKIP_PRIORITY` 给每个跳过原因定了优先级，只保留最相关的那条
（同时记录 `minutes_since_last_speak` / `min_interval_minutes` / `cooldown_minutes` /
`budget_used` / `budget_limit` 等数值便于对账）。

### 3. 静默窗口 fail-safe 与告警

**问题**：`_is_in_time_window` 解析失败时返回 `False`（= 不在窗口内），
于是**时间格式写错会让静默时段静默失效**，且没有任何提示。

**改动**：`_is_in_time_window` 增加 keyword-only 参数 `invalid_result`
（保持静态方法与既有调用/测试兼容）；`_tick` 通过 `_collect_invalid_time_keys()`
对 6 个时间配置项做校验，非法值记 warning（同一个值只告警一次），
并按 fail-safe 处理：**静默时段传 `invalid_result=True`**（视为静默，宁可不发）、
早晚安窗口保持默认 `False`（不触发）。

### 4. 跨午夜不再丢间隔

`affection_manager.reset_daily()` 原本会把 `last_speak_time` 清空，导致每天 0 点后
"距上次发言"的约束消失（23:58 刚发过、00:08 又能发）。现已不再清空该字段
（`today_speak_count` 与当日早晚安/想念标记仍照常重置）。

### 5. 文档澄清

README 新增「[主动发言节奏：哪个旋钮管哪个触发](#主动发言节奏哪个旋钮管哪个触发)」对照表，
并在配置项描述里标注 `miss_trigger_hours_*`「仅对想念生效」、
`user_cooldown_minutes` 的 60 分钟上限；WebUI 文案同步（含 en/ja 翻译）。

### 验证

- `test_mailove_v242.py`（测试区）：**35 项通过** —— 最小间隔门控（含豁免开关与关闭状态）、
  静默 fail-safe（非法静默/非法早晚安窗口/告警节流）、决策日志本体（字段、record_skips、
  按天落盘、过期清理）、`reset_daily` 保留 `last_speak_time`、入队失败如实记录。
- 回归：插件自带 76 项 + v2.4.1 补丁 23 项，全部通过。

---

## v2.4.3 改动明细（cateye）

本节是上文「Fork 改动说明」第 6 项的详细版。

### 1. 决策日志覆盖"所有主动发言行为"

| 事件 | 来源 | 为什么需要 |
|---|---|---|
| `trigger`（v2.4.2） | `Scheduler._tick` 各门控 | 只代表"已入队"，不代表真的说出口 |
| `spoken`（新） | `maisaka.replyer.after_response` 的 90 秒窗口回执 | 确认 planner **真的生成并发送**了，并标注触发类型（`plugin.py` `on_replyer_after_response`） |
| `tool`（新） | `mai_lover_send_message` Tool 发送成功 | 这条路径不走巡检，此前完全不在日志里 |
| `info`（新） | `Scheduler._log_external_schedule_status` | 外部日程模式下不生成日程，读不到节点就永远没有「日程节点分享」 |

`decision_logger` 相应新增 `ACTION_SPOKEN` / `ACTION_INFO`；`record_skips=false` 只影响 `skip`。

### 2. 外部日程状态可见

- `ExternalScheduleSource` 新增 `last_status` / `last_node_count`
  （`unavailable` / `cached` / `fresh` / `empty` / `error`），在 `get_today_nodes` 内更新。
- `ScheduleGenerator.refresh_external_schedule` 由返回 `None` 改为返回状态字典
  （`mode` / `result` / `nodes` / `cached_total` / `detail`；非外部模式 `internal/noop`）。
- `Scheduler._log_external_schedule_status` 只在**状态或节点数变化**时记一条 `info`，
  但 `empty` / `error` / `exception` **每次都记**。
  其中 `fresh` 与 `cached` 必须归并为同一状态：2 分钟 TTL + 10 分钟巡检会让两者每轮交替，
  否则会变成 144 行/天的噪声（这一点在测试里专门加了用例）。

### 3. `/mai_diag` 自诊断

- `Scheduler` 新增 `patrol_status()` / `is_patrolling` / `get_last_trigger_intent()`；
  巡检任务句柄现在被保存（`start()` 与 `start_patrol()` 两处），用来回答"巡检到底在不在跑"。
- `plugin.py` 新增 `_build_patrol_status_line()`：巡检状态 / 上次巡检时间 / 间隔 /
  `stream_id` 是否解析 / 主动开关 / 日程来源与今日节点数 / 外部拉取状态。
  它同时用于**汇总行**与**空日志提示**——这正是本次改动的起因：
  线上拿到"最近 3 天没有主动行为决策记录"却无法判断是刚加载还是巡检没跑。

### 验证

- `test_mailove_v243.py`（测试区）：**37 项通过** —— 外部日程五种状态（fresh/cached/empty/
  error/exception，含 API 异常折叠为 error 与调度器侧 exception 兜底）、状态变化才记日志、
  失败每次都记、`spoken` 回执（含超窗口不记）、Tool 主动发消息进日志、
  `/mai_diag` 四类计数与自诊断行、空日志提示附带状态行。
- 回归：v2.4.2 用例 38 项 + v2.4.1 补丁 23 项 + 插件自带 76 项，全部通过。

---

## v2.5.0 改动明细（cateye）

本节是上文「Fork 改动说明」第 7 项的详细版。源码里所有相关改动都用
`[LOCAL-PATCH:cateye]` 注释标记（在 `plugin.py` 里 grep 该标记即可定位）。

### 1. 注入作用域：默认只在目标私聊注入恋人上下文

| 会话 | 注入内容 |
|---|---|
| `target_qq` 的私聊（`session_id == 缓存的私聊 stream_id`） | 日期 + 当前状态 + 好感度（标注用户） |
| 已知私聊、两条消息的发送者 QQ 是目标用户（stream_id 变更后的兜底） | 同上 |
| 群聊 / 其它私聊（默认，开关关闭） | 只有日期 |
| 已知群聊（开关打开且最近 X 条用户消息的发送者 QQ 里出现目标用户） | 日期 + 当前状态 + 好感度（标注用户）+ 一句范围限定 |
| 会话类型未知 / 发送者反查不到 / stream_id 未解析 | 只有日期（**fail-safe**：宁可不注入，也不能撒到群里） |

- 判定入口：`MaiLoverPlugin._resolve_injection_scope()`；
  `maisaka.planner.before_request` 的 Hook 载荷本就带 `session_id`
  （宿主 `chat_loop_service.py` 传入，且与 `stream_id` 同值），旧代码从未读它。
- **判定只看 QQ 号**：新增 `sender_identity.py`，在
  `chat.receive.before_process`（新增 Hook，`mai_lover_sender_identity`，只记录不拦截）
  记录「消息 ID → 发送者 QQ 号」（`SenderCache`，24h TTL / 4096 容量）与
  「会话 ID → 是否群聊」（`SessionKindCache`）；planner 请求前用条目文本里的
  `msg_id` 反查（正则锚定 `<message …>` 前缀，兼容快照与旧投影）。
  昵称/群名片**完全不参与判定**——它们可被改名伪造，也会出现"某某的小号"这种形近名；
  昵称只用于注入文案的展示。机制与 `cateye_admin_identity` 同源。
- 窗口语义：回看最后 X 条**用户消息**（不是 parts 数，也不被 system/assistant 条目稀释），
  反查不到发送者的条目按"不是目标用户"处理。
- 主动发言回合的「回复目标约束」也套用同一门控（原先任何会话都会注入）。
- 昵称/群名片在解析私聊 `stream_id` 时顺带缓存（`_remember_stream_identity()`），
  同时兼容宿主 `chat.get_stream_by_user_id` 的两种返回形状
  （顶层平铺 / 包在 `stream` 里）与 `get_private_streams` 的三种形状。

### 2. 注入文案标注用户

**目标私聊**（不加任何范围说明——会话本身就确定了对象，上下文里没有第三方）：

```
【当前日期】今天是 2026-09-21（星期一），工作日。
【麦麦当前状态】现在 16:51，麦麦正在采购日用品。
【麦麦对用户 小美（QQ 100000000）的好感度】档位 2（热恋阶段，语气甜蜜亲密，可以肉麻撒娇）
```

**群聊档**（开关打开且最近 X 条用户消息的发送者里有目标用户）在此基础上多一行限定：

```
（当前是群聊：上述好感度与恋人语气仅在与小美（QQ 100000000）直接互动时使用；
对群里其他人保持普通、有分寸的关系，不要因为 TA 在场就忽略其他人。）
```

> 这行可以放心写"当前是群聊"：会话类型由入站消息记录（`SessionKindCache`），
> 群聊档只在确认为群聊时才注入，不会把别人私聊误标成群聊。

### 3. 节假日判断：修掉"联网 API 其实从未生效"

旧代码（`holiday_service.py`）取的是 `data["holiday"]["type"]`，而 timor.tech
把类型放在 `data["type"]["type"]`（0=工作日 1=周末 2=节日 3=调休），
`holiday` 字典里根本没有 `type` 键 → `holiday_type` 恒为 `-1` → 每次都走
"按星期几判断"的兜底。表现就是：**国庆节显示"工作日"，调休补班的周日显示"周末休息日"**。

现在的取数顺序：

1. 整年表（timor `/api/holiday/year/{year}`）：含 `holiday=false` 的补班日，落盘缓存 3 天；
2. 整年表备源（jiejiariapi `/v1/holidays/{year}`，字段 `isOffDay`）：
   只认"放假"与"周末却要上班"，工作日的小年等条目忽略，避免误判成调休；
3. 按天 API（timor `/api/holiday/info/{date}`）：整年表拉不到时使用（**类型层级已修正**）；
4. 本地星期几判断：全部失败时的兜底。

文案示例（2026 年实测，见下）：`国庆节假期` / `调休工作日（中秋节前补班）` /
`调休工作日（国庆节后补班）` / `工作日` / `周末休息日`。

### 验证

- 离线新增用例：
  - `tests/test_injection_scope.py`（15 项）：私聊注入与标注、别人私聊永不注入、
    群聊默认不注入、群聊开关打开后按 QQ 命中/窗口外/形近昵称不命中/发送者未知不命中、
    会话类型未知不进群聊档、stream_id 变更后的私聊兜底、入站记录本身。
  - `tests/test_sender_identity.py`（17 项）：缓存 TTL/容量、入站载荷提取、
    `msg_id` 前缀解析（快照/旧投影/正文里伪造的 `<message>` 不算前缀）、窗口语义。
  - `tests/test_holiday_service.py`（18 项）：文案映射、主备源解析、按天 API 层级、
    磁盘缓存、过期表保留、损坏缓存容错。
- 全部用例 `pytest` **126 项通过**（`python -B -m pytest -q`）。
- **端到端注入模拟**（真实 SDK + 真实插件类、模拟入站 Hook）：① 目标私聊注入且不加范围说明；
  ② 群聊默认只有日期；③ 群聊开关开且恋人 QQ 在窗口内 → 带群聊限定；④ 群里有人把昵称改成
  「小美的小号」→ **不注入**（QQ 不符）；⑤ 别人的私聊 → 不注入。
- **真实联网实测**（本机 `python -c` 直连两个数据源，2026 年真实安排）：

  | 日期 | 旧行为 | v2.5.0 输出 |
  |---|---|---|
  | 2026-09-20（周日，中秋前补班） | 周末休息日 ❌ | 调休工作日（中秋节前补班） ✅ |
  | 2026-09-21（周一） | 工作日 | 工作日 ✅ |
  | 2026-10-01（周四，国庆） | 工作日 ❌ | 国庆节假期 ✅ |
  | 2026-10-10（周六，国庆后补班） | 周末休息日 ❌ | 调休工作日（国庆节后补班） ✅ |

  整年表 2026 年抓到 39 条特殊日期；2027 年安排尚未公布时按普通工作日/周末回答，
  公布后由 3 天 TTL 自动刷新。

---

## v2.6.0 改动明细（cateye）

本节是上文「Fork 改动说明」第 8 项的详细版。

### 1. 屏幕感知：先 VLM，再激活 planner

**旧流程**（v2.4.0 – v2.5.0）：`_trigger_morning` / `_trigger_night` / 想念分支各自
调 `_get_computer_context()`，把「（看了一眼恋人的电脑：在写代码）」**拼进
`proactive.trigger` 的 `reason`**，等于往 planner 里塞一段说明；日程节点分享与
日常巡检根本不看屏幕。

**新流程**（v2.6.0）：所有触发都走 `_trigger_planner`，它在 `proactive.trigger`
**之前**统一调 `_publish_screen_narration()`：

```text
_tick 判定通过
   └─ _trigger_planner(intent, reason)
        ├─ _publish_screen_narration()      ← 截图 + VLM + 拼旁白 → screen_context 缓存
        └─ ctx.maisaka.proactive.trigger()  ← 这时才唤醒 planner（reason 不含电脑状态）
```

旁白由 Hook 在**请求时**插进上下文（见下一节），所以 planner 是被唤醒后**自然读到**的，
不是被塞进提示词的。想念的取屏时机也从"把关前"挪到"把关后"，被驳回不再白截一张。

`_publish_screen_narration()` 内部按 `peek_screen()` 的状态选模板：

```text
peek_screen() → ok       → narration_template           （{description} = 视觉转述）
              → offline  → offline_narration_template   （「TA的电脑没开」，v2.5.0 语义）
              → failed   → failed_narration_template    （「没看清TA在干什么」，v2.5.0 语义）
              → None     → 不注入（功能关闭 / 整体超时，与 v2.5.0 一致）
```

「电脑没开 / 没看清」在 v2.5.0 是拼进 `reason` 的括号文案，现在改为走同一条旁白通道：
麦麦照样知道"这次没看到"，但 planner 提示词里不再多一段说明。
对应模板**留空 = 那种情况不注入**（只想要"看清楚了"就清空后两项）。

### 2. 旁白怎么进上下文：尾随锚点（`screen_context.py`）

**先说两个走不通的路**（都在宿主源码里验证过）：

| 方案 | 为什么不行 |
|---|---|
| MessageGateway 注入「bot 身份」消息入库 | 私聊的 `session_id = md5(platform + user_id + "private")`（`src/common/utils/utils_session.py`）由**发送者**决定。`user_id` 写成机器人自己 → 算出的是「bot 跟自己聊天」的幽灵流，**落不进恋人私聊流**；宿主还会重算 `session_id`（`chat/message_receive/bot.py`），插件传什么都不管用 |
| 同上但 `is_notify=True` | 宿主恢复上下文时对通知消息直接 `continue`（`src/maisaka/runtime.py`），**planner 读不到** |
| `ctx.maisaka.context.append` | 能精确落到指定流，但它是**持久**追加进 `runtime._chat_history` 的 user 角色消息，长期占一个上下文槽位，还可能把真实消息挤掉 |

**实际做法**：请求级注入，不碰宿主历史。

```text
触发时：  store.publish(恋人私聊流, "现在是…你看了眼…的电脑屏幕，TA正在：…")

每轮 planner / replyer 请求：
  1. 在载荷 items 里找锚点 = 触发那一刻**最后一条真实聊天消息**
     （跳过宿主 <plugin_proactive_task> 插件块、本插件注入的旁白、system/工具条目）
  2. 首轮找到 → 记下它的身份（meta.item_id + 前缀里的 msg_id），两个都记，命中其一即算同一条
  3. 把旁白作为 **AssistantMessageItem（bot 身份）** 插到锚点**之后**
  4. 某轮该通道找不到锚点（一般是超出上下文条数）→ **只作废该通道**
  5. planner 与 replyer 都作废 → 清缓存，此后不再扫描
  6. 另有 context_ttl_minutes（默认 90 分钟）兜底，避免旧屏幕信息被当成现在的事
```

**为什么 planner / replyer 要分开**：replyer 的上下文由 planner 选定后再组织，
条数与裁剪都可能不同——同一条锚点可能只在其中一边还在。所以两个通道各存一份
锚点状态，互不牵连。

**注入作用域**：`_apply_screen_narration` 复用 v2.5.0 的 `_resolve_injection_scope`，
只有 `private` 档才继续，**群聊（哪怕开了群聊注入）与其他会话一律不注入**，
屏幕内容不会漏到非恋人的聊天流里。

### 3. 想念的「未来有日程就不打扰」改成开关

旧代码 `scheduler.py` 里写死 `elif self._has_future_schedule(2, now):`。
问题不是它错，而是**默认参数组合下想念不可达**：

- 模板日程节点 08:30 – 22:00 每 1~2 小时一个 → 这道闸门一天里只在
  `00:00–06:29` 与 `22:00–23:59` 放行，而 `00:00–08:00` 又是静默时段；
- 于是想念唯一窗口 = 晚安窗口，而晚安一触发就刷新 `last_speak_time`，
  把「主动发言最小间隔 240 分钟」重置 → 想念被压到 02:00 之后 = 静默；
- 外部日程模式节点更密，这道闸门几乎恒为真 → **想念永不触发**。

全天仿真（每 10 分钟一 tick，30 天）：

| `min_trigger_interval_minutes` | 截图来源（旧行为） |
|---|---|
| 0 / 30 | morning×30、night×30、**miss×17** |
| 60 | morning×30、night×30、miss×13 |
| 120 | morning×30、night×30、**miss×0** |
| 240（默认） | 只有 morning×30 |

现在 `miss_avoid_future_schedule` **默认 false** = 不看日程；需要旧行为就打开，
窗口长度用 `miss_future_schedule_hours`（默认 2.0）调。

### 验证

- 新增 `tests/test_screen_context.py`（27 项）：文案模板 / 空模板 / 坏模板回退、
  三种看屏幕结果（看清楚 / 电脑没开 / 没看清）各自的旁白、自定义模板、
  留空模板 = 那种情况不注入、`peek_screen()` 返回 None 时完全不注入、
  锚点选取（跳过宿主插件块、自身旁白、宿主每轮重建的合成 user 条目）、
  首轮锁定 + 跨轮复用、`msg_id` 重建后仍命中、单通道失效与双通道清空、TTL 过期、
  未知流不注入、planner/replyer 分通道独立、非恋人会话（群聊 / 其他私聊 / 会话未知）
  一律不注入、触发未入队时撤掉旁白、无缓存时 Hook 安全空转。
- 插件自带离线测试 **153 项通过**（原 126 项全部保留，`cateye_client` 的 6 项按
  `peek_screen()` 新 API 重写并补了状态断言）。
- 全天巡检仿真：开关关闭 → 想念正常触发；开关打开 → 想念被 `future_schedule` 挡住
  （旧行为）；一天 10 次主动触发**全部**取到屏幕旁白。
- 独立子代理对抗性自检：宿主 `maisaka.replyer.before_model_request` 契约成立
  （HookSpec + `allow_kwargs_mutation=True`，`modified_kwargs` 会被整体采用；
  **必须回传 `item_schema_version`**，插件的 `{**kwargs, …}` 已保留），
  旁白条目快照通过宿主 `deserialize_prompt_items` 全量校验。
  自检抓到的锚点问题（见上「合成 user 条目」）已在发布前修掉并补了回归用例。

---

## 依赖

- **Python** ≥ 3.10
- **MaiBot Plugin SDK** ≥ 2.5.4
- **httpx** — 节假日 API
- **pydantic** — 配置模型（随 SDK 安装）

---

## 许可证

MIT
