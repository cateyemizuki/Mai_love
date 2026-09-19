# 麦麦恋人（MaiLover）

**私聊专用虚拟恋人插件** — 基于 MaiBot Plugin SDK 2.5.4

> 麦麦有自己的生活 · 主动找你聊天 · 好感度调节温度

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

> 上游原有无外部日程开关，所有改动向后兼容：不开启 `use_external_schedule` 时行为与上游完全一致。

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
- **主动聊天** — scheduler 定时提醒 planner"可以考虑说话了"，planner 自主决定发不发
- **早晚安仪式** — 早安/晚安时间窗触发 planner，麦麦主动跟你说早/晚安
- **想念机制** — 你太久没理她，麦麦会跑来说想你：触发时长是**可配置区间**
  （沉默越久越容易触发），触发前还可让 **LLM 以角色身份把关**，此刻开口
  不自然就驳回，不再"被代码推着硬接话题"（每天最多 1 次）
- **"在干嘛"查询** — 你问"在干嘛"时，planner 调用 Tool 查麦麦当前活动，自然回答
- **静默时段** — 配置睡觉时间，那段时间麦麦完全安静
- **好感度系统** — 3 档位影响语气温度（熟悉/亲密/热恋）
- **恋人电脑联动**（可选，需 cateye 插件）— 想念/早晚安触发时看一眼恋人的电脑：
  已连接则截图并用视觉模型转述"TA在干什么"给麦麦；电脑没开就告诉麦麦「TA的电脑没开」
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
target_qq = 2335260621  # ← 改成你的 QQ 号
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
| `schedule.check_interval_minutes` | 5 | 巡检间隔（分钟） |
| `schedule.daily_max_speak` | 5 | 每日主动触发上限 |
| `schedule.user_cooldown_minutes` | 5 | 用户发言后冷却（分钟） |
| `schedule.proactive_trigger_enabled` | true | 麦麦会不会主动找你 |
| `schedule.use_external_schedule` | false | 使用外部日程：读取自主规划插件，本插件清空缓存且不再生成 |

### 概率设置
| 参数 | 默认值 | 说明 |
|------|--------|------|
| `probability.default_speak_rate` | 0.6 | 日常主动找你说话的概率 |
| `probability.miss_speak_rate` | 0.8 | 想念触发概率 |
| `probability.activity_trigger_rate` | 0.6 | 日程节点到点分享的概率 |

### 时间窗口
| 参数 | 默认值 | 说明 |
|------|--------|------|
| `time_windows.morning_start` | 06:00 | 早安窗口开始 |
| `time_windows.morning_end` | 09:00 | 早安窗口结束 |
| `time_windows.night_start` | 22:00 | 晚安窗口开始 |
| `time_windows.night_end` | 23:59 | 晚安窗口结束 |
| `time_windows.miss_trigger_hours_min` | 4.0 | 想念触发区间下限（小时）：沉默不足绝不触发 |
| `time_windows.miss_trigger_hours_max` | 8.0 | 想念触发区间上限（小时）：沉默超过后时长条件必满足 |
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
| `cateye.enabled` | false | 想念/早晚安触发时查看恋人电脑（需安装 cateye.connect-hub 插件） |
| `cateye.screenshot_blur` | 0 | 截图模糊半径（0-64），0=清晰 |
| `cateye.vlm_task` | vlm | 截图理解使用的视觉模型任务名 |
| `cateye.timeout_seconds` | 20 | 查看电脑的整体超时（秒），超时不阻塞触发 |
| `cateye.describe_prompt` | （见 config.toml） | 截图的视觉理解提示词 |

工作方式：触发前先查 `cateye.connect-hub.status`——未连接（含插件未安装）时把
「恋人的电脑没开」拼进提示词；已连接则截图交给视觉模型转成一句话描述
（如"在打游戏"），视觉理解不可用时自动降级为"电脑开着但没看清"。

### LLM 调用日志
| 参数 | 默认值 | 说明 |
|------|--------|------|
| `llm_log.enabled` | true | 记录插件发起的模型请求回复（事件/时间/内容） |
| `llm_log.retention_days` | 3 | 日志保留天数，过期自动清理 |

事件来源包括：`schedule_generation`（日程生成）、`miss_confirm`（想念把关，
可看到每次驳回的原始回复）、`tool_send_message`（Tool 生成消息）、
`cateye_screen_describe`（屏幕理解）。用 `/mai_llm_log [天数]` 通过合并转发查看，
每次请求一条消息（标注时间、事件、模型与成功状态）。

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
├── message_service.py     # 消息发送 + 情绪后缀
├── holiday_service.py     # 节假日 API
├── schedule_generator.py  # 日程生成 + 活动查询
├── scheduler.py           # 调度引擎（触发 planner）
├── mai_template.json      # 麦麦作息骨架
├── data/                  # 运行时数据目录（好感度/日程缓存，自动生成）
└── README.md              # 本文件
```

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

---

## 依赖

- **Python** ≥ 3.10
- **MaiBot Plugin SDK** ≥ 2.5.4
- **httpx** — 节假日 API
- **pydantic** — 配置模型（随 SDK 安装）

---

## 许可证

MIT
