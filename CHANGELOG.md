# 更新日志 / Changelog

本文件记录**麦麦恋人（MaiLover）本 fork（[cateyemizuki/Mai_love](https://github.com/cateyemizuki/Mai_love)）**的版本变更。
格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.1/)，版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

> **溯源**
>
> - **原作者 / 上游项目**：[octmicy/Mai_love](https://github.com/octmicy/Mai_love)（MIT，版权归原作者所有）。
> - **现维护者**：[cateye](https://github.com/cateyemizuki)，自 v2.4.1 起接手维护。
> - 插件 ID `maibot-community.mai-love` **保持不变**，其他插件依赖它调用公开 API。
> - 本 fork 的 2.3.0 – 2.4.0 改动已在 README「Fork 改动说明」中按版本记录；本文件自 v2.4.1 起接管版本变更记录。

## [2.6.0] - 2026-09-26

**主题：先看屏幕，再叫醒 planner；顺便把「想念永远不触发」放出来。**

### 变更（Changed）

- **屏幕感知改成「先 VLM，再激活 planner」**。原先的做法是把转述文本拼进
  `proactive.trigger` 的 `reason` 提示词，等于往 planner 里塞一段说明；现在改为：
  触发时先取屏幕（截图 + 视觉转述），把旁白发布到新的 `screen_context` 缓存，
  **然后**才调 `maisaka.proactive.trigger`（`reason` 里不再含电脑状态）。
- **屏幕感知覆盖全部主动触发**：早安 / 晚安 / 想念 / 日程节点分享 / 日常巡检。
  此前只有早晚安与想念会看屏幕（`_trigger_morning` / `_trigger_night` / 想念分支
  各自调用），日程节点与日常巡检完全不看。
- **想念的屏幕感知挪到 LLM 把关之后**：原先把关前就截图，被驳回等于白截一张；
  现在统一由 `_trigger_planner` 在真正触发时取。副作用是
  `_confirm_missing_with_llm` 不再收到电脑状态（该参数已移除），
  把关提示词里少了一行「TA 在不在电脑前」的参考信息。
- `[cateye]` 新增四个配置项，旁白**不再由代码写死**：
  - `narration_template`（看清楚时，占位符 `{date}` / `{time}` / `{user_name}` / `{description}`）；
  - `offline_narration_template`（电脑没开时，占位符同上但无 `{description}`）；
  - `failed_narration_template`（截图/理解失败时，同上）；
  - `context_ttl_minutes`（旁白存活上限，默认 90 分钟，0 = 不限）。
- **「电脑没开 / 没看清」保留，但换通道**：v2.5.0 是把
  `（恋人的电脑没开）` / `（恋人的电脑开着，但没看清TA在干什么）` 拼进 `reason`；
  v2.6.0 改为走同一条旁白通道（三个模板对应三种看屏幕结果）。
  语义不变（麦麦照样知道"这次没看到"），但 planner 提示词里不再多一段说明。
  对应模板**留空 = 那种情况不注入**（只想要"看清楚了"就清空后两项）；
  功能关闭 / 整体超时仍按不注入处理（与 v2.5.0 一致）。
- `cateye_client` 的返回值从「字符串」改为 `ScreenPeek` 状态对象
  （`ok` / `offline` / `failed`）——"看不成"现在要分两种说法，调用方需要区分，
  不能再靠"空串 = 没看成"来猜。
- `[cateye] enabled` 的说明改为「每次主动触发时查看 + 三种结果各自的旁白」，
  不再写「想念/早晚安触发时」。

### 新增（Added）

- **`screen_context.py`：屏幕旁白的「尾随锚点」注入**。
  旁白不是一次性追加进宿主历史，而是**请求级**注入：
  1. 触发时 `publish(stream_id, text)`；
  2. 之后**每一轮** planner / replyer 请求，在载荷 `items` 里定位锚点
     ——触发那一刻上下文里**最后一条真实聊天消息**（用户或 bot 发的）。
     排除项：宿主的 `<plugin_proactive_task>` 插件块、本插件自己注入的旁白、
     system/工具/推理/参考消息条目，以及**宿主每轮重建的「合成 user 条目」**
     （当前时间、planner 最终提醒、replyer 回复要求——它们 `item_id` 每轮都是新 uuid，
     一旦被当成锚点，第二轮就找不回来，旁白只会注入一轮）。判据是真实用户消息带
     `<message msg_id="…">` 前缀，合成条目没有；
  3. 找到就把旁白作为一条 **`AssistantMessageItem`（bot 身份）** 插到锚点**之后**；
  4. 某通道的上下文里锚点消失了（一般是超出上下文条数）→ **只作废该通道**；
     planner 与 replyer 的上下文不一定相同，因此**分开识别、分开失效**，
     两者都作废后整条缓存清除，此后不再扫描；
  5. 另有 `context_ttl_minutes` 兜底，避免几小时后还把「TA 正在写代码」当成现在的事。
  - 为什么不直接 `ctx.maisaka.context.append`：它是**持久**追加进
    `runtime._chat_history` 的 user 角色消息，会长期占一个上下文槽位，
    也可能把真实消息挤掉；请求级注入不污染宿主历史。
  - 为什么不走 MessageGateway「入库不真发」：私聊的 `session_id` 由**发送者**
    `user_id` 决定（`utils_session.py`），`user_id=机器人自己` 算出来的是
    「bot 跟自己聊天」的幽灵流，**进不了恋人的私聊流**；而 `is_notify=True`
    的记录又会在宿主恢复上下文时被 `continue` 跳过，planner 读不到。
  - 新增 Hook `maisaka.replyer.before_model_request`（旁白注入），
    `maisaka.planner.before_request` 同步支持。
- **`time_windows.miss_avoid_future_schedule`（默认 false）** 与
  `time_windows.miss_future_schedule_hours`（默认 2.0）：
  「未来 2 小时内有日程节点就先不打扰想念」此前是**硬编码**（`scheduler.py` 里
  写死的 `_has_future_schedule(2, now)`）。外部日程模式下节点密集，
  这道闸门几乎恒为真，再叠加「主动发言最小间隔 240 分钟」与晚安窗口，
  想念**实际上永远不会触发**（30 天 × 每 10 分钟一 tick 的仿真：
  `min_interval=120` 时 miss 命中 0 次，`=0/30` 时才命中 17 次）。
  现在默认关闭 = 放行想念；需要旧行为可手动打开，窗口长度可配。

### 移除（Removed）

- `cateye_client.get_computer_context()` 与其 `TEXT_COMPUTER_OFFLINE` /
  `TEXT_COMPUTER_BLURRED` 两个常量删除，改为 `peek_screen()` + `ScreenPeek`
  （两个常量的文案成为 `[cateye] offline_narration_template` /
  `failed_narration_template` 的默认值，见「变更」）。

### 修复（Fixed）

- **锚点被宿主的「合成 user 条目」抢走**（自检阶段发现）：宿主在 planner / replyer
  载荷末尾会追加一批 `ContextItemBuilder` 现造的 user 条目（当前时间、最终提醒、
  回复要求），它们文本非空、`item_id` 每轮都是新 uuid，会被误判成"真实消息"当上锚点，
  下一轮找不回来 → 旁白实际只注入一轮。现在 user 条目必须有
  `<message msg_id="…">` 前缀才算真实聊天消息（真实用户消息由宿主
  `build_planner_user_prefix_from_session_message` 写前缀，合成条目没有）。
- **触发未入队时旁白不回滚**：`proactive.trigger` 返回
  `{"success": False}`（如「未找到已存在的聊天流」）时，刚发布的旁白会留在缓存里
  （最长 `context_ttl_minutes`），可能被之后某轮**普通对话**的请求锚上并注入——
  用户正在聊天，麦麦突然说「你看了眼…的电脑屏幕」。现在入队失败 / 抛异常都会
  `discard` 掉该流的旁白。
- **空 `narration_template` 静默关掉功能**：`"".format()` 不抛异常、返回空串，
  旁白被当成空文本丢弃，与「占位符写坏会自动回退」的说明不符。现在空模板直接走内置默认文案。
- **`[cateye]` 的 WebUI 说明仍是 v2.5.0 旧行为**：`enabled` 还写着「想念回复与
  早安/晚安触发时…未连接则告诉 LLM『恋人的电脑没开』」，`vlm_task` 还写着
  「降级为『电脑开着但没看清』」——这些行为在 v2.6.0 已不存在，照旧描述排查会误导。
- `_confirm_missing_with_llm` 的 `computer_context` 参数已无来源（见上），
  连带移除，避免留下"看起来还能传值、实际恒为空"的死参数。
- `_publish_screen_narration` 的前置判空从 `try` 外移进 `try` 内，
  与「任何异常都不外抛、绝不阻塞主动触发」的承诺一致。

### 验证

- 插件自带离线测试 **153 项通过**（原 126 项 + 新增 `test_screen_context.py` 27 项：
  文案模板 / 空模板 / 坏模板回退、三种看屏幕结果各自的旁白（看清楚 / 电脑没开 / 没看清）、
  自定义模板、留空模板 = 那种情况不注入、`peek_screen()` 返回 None 时完全不注入、
  锚点选取（跳过插件块、自身旁白、合成 user 条目）、跨轮复用锚点、`msg_id` 重建后仍命中、
  单通道失效与双通道清空、TTL 过期、未知流不注入、planner/replyer 分通道独立、
  非恋人会话（群聊/其他私聊/会话未知）一律不注入、触发未入队时撤掉旁白、
  无缓存时 Hook 安全空转）。`cateye_client` 的 6 项按 `peek_screen()` 重写并补状态断言。
- 全天巡检仿真（测试区 `_work_mailove_v260/probe_v260.py`）：开关关闭时想念正常触发；
  开关打开时想念被 `future_schedule` 挡住（旧行为）；一天内 10 次主动触发**全部**
  取到屏幕旁白。
- 独立子代理对抗性自检：确认宿主 `maisaka.replyer.before_model_request` 契约成立
  （HookSpec + `allow_kwargs_mutation=True`，载荷含 `items`/`session_id`，
  `modified_kwargs` 会被整体采用；**必须回传 `item_schema_version`**，插件
  `{**kwargs, ...}` 已保留），旁白条目快照通过宿主 `deserialize_prompt_items` 全量校验。

## [2.5.0] - 2026-09-21

### 新增（Added）

- **`[injection]` 配置节**（上下文注入）：
  - `group_affection_enabled`（默认 **false**）：群聊是否也注入「恋人当前状态 / 好感度」；
  - `group_recent_user_messages`（默认 **15**，1~200）：群聊注入的回看条数 X——
    只有上下文最后 X 条用户消息的**发送者 QQ 号**里出现目标用户才注入。
- **`sender_identity.py`：发送者身份缓存（判定只看 QQ 号）**。
  宿主写进 planner 上下文的真实消息只有 `msg_id` 与显示名（`user="昵称"` /
  `group_card="群名片"`），**没有 QQ 号**，而名字谁都能改（还会出现「某某的小号」
  这种形近名）。因此新增入站 Hook `chat.receive.before_process`
  （`mai_lover_sender_identity`，只记录不拦截）维护两份缓存：
  「消息 ID → 发送者 QQ 号」（`SenderCache`，24h TTL / 4096 容量）与
  「会话 ID → 是否群聊」（`SessionKindCache`）；planner 请求前用上下文条目里的
  `msg_id` 反查发送者，**按 QQ 号判定**，昵称/群名片只用于注入文案展示。
  机制与 `cateye_admin_identity` 同源。
- 注入文案**标注用户**：「【麦麦对用户 小美（QQ 100000000）的好感度】档位 2（…）」——
  好感度属于哪一位用户一目了然。群聊档再附一句「恋人语气只在与 TA 直接互动时使用」的限定；
  **私聊档不加范围说明**（私聊会话本身已确定对象、上下文里没有第三方，多写只是白烧 token）。
- 节假日服务改为**整年放假安排表**（含调休补班日）+ 磁盘缓存
  （`holiday_cache.json`，3 天 TTL，主备两个数据源）。

### 修复（Fixed）

- **注入污染群聊**：`maisaka.planner.before_request` 的注入原本完全不看 `session_id`，
  任何会话（含群聊）都会被塞进「【当前状态】…【对用户的好感度】档位 2（热恋阶段…）」，
  文案又不带用户标识。线上日志（2026-09-20）实测 35 条注入 **全部落在群聊会话**，
  模型推理里出现「it's in 热恋阶段 with the admin (好感度档位2)」——群聊回复被私聊
  恋人设定带跑。现在：日期行全会话注入；恋人上下文默认**只注入目标用户私聊**；
  群聊需显式开启且满足"最近 X 条用户消息的发送者 QQ 号里出现目标用户"才注入；
  `stream_id` 未解析、会话类型未知、发送者反查不到时一律 fail-safe（只注入日期）。
  主动发言回合的「回复目标约束」套用同一门控（原先同样不分会话）。
- **节假日判断实际从未生效**：旧代码取 `data["holiday"]["type"]`，而 timor.tech 把类型
  放在 `data["type"]["type"]`（0=工作日 1=周末 2=节日 3=调休），`holiday` 里没有 `type` 键
  → 恒为 `-1` → 每次都退回"按星期几判断"。表现为**国庆节显示"工作日"、
  调休补班的周日显示"周末休息日"**（线上 2026-09-20 正是中秋前补班的周日）。
  现在改为整年表 + 按天 API 层级修正 + 本地兜底三级降级，并正确处理 `type == 3`（调休）。

### 变更（Changed）

- `HolidayService.__init__` 新增可选参数 `data_dir`（提供时启用整年表磁盘缓存）；
  新增 `refresh(year)` / `table_size(year)`。
- `plugin.py` 的 `_resolve_stream_id` 顺带缓存目标用户的昵称/群名片
  （`_remember_stream_identity`，**仅用于文案展示**），并兼容
  `chat.get_stream_by_user_id` 的两种返回形状与 `get_private_streams` 的三种形状；
  新增 `_stream_payload` / `_iter_stream_candidates` 辅助。
- 新增私聊兜底：若 `session_id` 与缓存的私聊 `stream_id` 不一致、但该会话已知是私聊
  且最近一条用户消息的发送者 QQ 就是目标用户（宿主重建会话的场景），仍按目标私聊注入。
- `on_unload` 额外清空发送者/会话类型缓存。
- `/mai_config` 与 `mai_lover_config` Tool 的摘要新增「恋人上下文注入」作用范围。
- 文本变化：节假日文案改为 `国庆节假期` / `调休工作日（中秋节前补班）`
  （原为 `{name}假期`，且调休日错误地落到本地星期几判断）。

### 兼容性说明

- **默认行为变化（重要）**：群聊不再注入「当前状态 / 好感度」——这正是本次修复的目的。
  升级后群聊里只保留【当前日期】。需要旧行为请在 WebUI「上下文注入」里打开
  「群聊也注入恋人上下文」（并注意它是"最近 X 条里出现 TA 才注入"，不是无条件注入）。
- **无破坏性配置变更**：`config.toml` 只新增 `[injection]` 两个字段，旧字段与旧值全部保留。
- **新增 Hook**：`chat.receive.before_process`（只记录「消息 ID → 发送者 QQ 号」与
  「会话 ID → 是否群聊」，不做任何拦截与改写）。群聊注入判定与文案里的
  "当前是群聊"都依赖它；插件启动前就在上下文里的历史消息因此不参与判定。
- `config_version` 与 manifest 版本同步：**2.4.3 → 2.5.0**。
- 许可证不变（MIT），上游版权声明原样保留。

## [2.4.3] - 2026-09-20

### 新增（Added）

- **决策日志覆盖「所有主动发言行为」**（此前只记巡检的发起与跳过）：
  - `spoken`：planner **确认真的生成并发出去了**（由 `maisaka.replyer.after_response`
    在触发后 90 秒窗口内回执），并标注是哪种触发（`morning/night/miss/daily/activity`）。
    「触发」只代表已入队，这一条才是"确实说出口了"。
  - `tool`：planner 通过 `mai_lover_send_message` Tool 主动发消息（不走巡检的那条路径）。
- **外部日程拉取结果进日志**（`info` / `schedule_source`）：
  `external_schedule_fresh | cached | empty | error | exception`。
  这一条正是"日程来自外部插件"时最需要的信息——外部日程模式下本插件不生成日程，
  若拉不到节点，「日程节点分享」永远不会触发，以前只能靠猜。
  `fresh` 与 `cached` 归并为同一状态（否则 2 分钟 TTL 与 10 分钟巡检会让两者每轮交替，
  变成 144 行/天噪声）；**节点数变化、或出现 empty/error/exception 时记录**（失败每次都记）。
- **`/mai_diag` 新增运行状态行**：巡检是否在跑 / 上次巡检时间 / 巡检间隔 /
  `stream_id` 是否解析 / 主动开关 / 日程来源与今日节点数 / 外部拉取状态。
  **日志为空时也带这一行**，并直接点明"若巡检=未运行，说明 stream_id 没解析出来，
  主动发言整条链路都没跑"——不用再靠猜。

### 修复（Fixed）

- **`ScheduleGenerator.refresh_external_schedule` 改为返回状态字典**
  （`{"mode","result","nodes","cached_total","detail"}`；非外部模式返回 `internal/noop`）：
  此前无返回值，调用方无法判断"外部日程到底读到没有"。
- **`ExternalScheduleSource` 新增 `last_status` / `last_node_count`**
  （`unavailable` / `cached` / `fresh` / `empty` / `error`），供日志与状态行读取。

### 变更（Changed）

- `Scheduler` 新增 `patrol_status()`、`is_patrolling`、`get_last_trigger_intent()`；
  巡检任务句柄现在会被保存（用于回答"巡检到底有没有在跑"）；
  `clear_last_trigger_time()` 同时清除触发类型。
- `decision_logger` 新增动作常量 `ACTION_SPOKEN` / `ACTION_INFO`
  （`record_skips=false` 只影响 `skip`，不影响这两类）。

### 兼容性说明

- **只增不改**：新动作类型只影响日志内容与 `/mai_diag` 展示；
  `refresh_external_schedule` 由返回 `None` 改为返回 dict，上游调用方均忽略返回值，向后兼容。
- **无新增配置项**，无需迁移；`config_version` 与 manifest 版本同步：**2.4.2 → 2.4.3**。
- 许可证不变（MIT），上游版权声明原样保留。

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
