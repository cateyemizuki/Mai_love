"""麦麦恋人插件 - 常量定义模块

包含情绪后缀池、LLM Prompt 模板、节假日降级判断等常量。
v2.0.0: 移除发言生成相关常量（SPEAK_GENERATION_PROMPT/KEYWORDS/FALLBACK_MESSAGES），
主动发言统一走 planner 触发，不再由插件自行调 LLM 生成。
"""

# 情绪锚点后缀池（按好感度档位）
AFFECTION_SUFFIXES: dict[int, list[str]] = {
    0: ["~", "哦", "呢", "哈"],
    1: ["~❤️", "啦！", "嘿嘿~", "嗯呐~"],
    2: ["~抱抱", "亲亲~", "想你啦~", "mua~"],
}

# 档位2 额外括号小剧场（10% 概率触发）
BRACKET_THEATERS: list[str] = [
    "(虽然知道你在忙但还是想你了呢)",
    "(偷偷亲你一下应该没人发现吧)",
    "(今天也是想见你的一天)",
    "({name}今天也超级喜欢你哦~)",
]

# LLM System Prompt: 日程生成（v2.0.0 重写 — 生成麦麦虚拟日常活动）
SCHEDULE_GENERATION_PROMPT: str = """你是虚拟恋人"{lover_name}"。请根据以下信息生成你今天一天的活动安排。

今天是 {date}，{holiday_info}。
你的人设性格：{personality}

你的作息骨架如下（在此基础上微调时间并添加随机活动）：
{mai_template}

请生成一个 JSON 数组，每个节点包含：
- time: 时间（HH:MM 格式）
- activity: 你在这个时间正在做什么（一句话描述，自然口语化）

要求：
1. 保留骨架中的核心作息节点，时间可微调 ±30 分钟
2. 根据工作日/周末调整活动风格：工作日偏规律，周末偏慵懒休闲
3. 穿插 1-2 条"想用户了""等用户消息"等状态活动（随机时间）
4. activity 描述要符合你的人设性格，自然生动像真人
5. 只返回 JSON 数组，不要其他内容

示例格式：
[{{"time": "08:30", "activity": "赖床中，闹钟响了还在赖"}}]"""

# ── 想念触发提示词（v2.3.0：可在 WebUI 配置中查看与修改）──────────────
# 1) 触发时传给 planner 的 reason（占位符：{lover_name} / {hours}）
MISS_REASON_PROMPT_DEFAULT: str = (
    "你已经有 {hours} 个小时没收到用户的消息了，你有点想TA了。"
    "可以主动开口问问近况、表达一下想念；但不必强行找话题，"
    "如果觉得此刻开口不自然，平淡地打个招呼也可以。"
)

# 2) 触发前 LLM 驳回检查的提示词（占位符：{lover_name} / {personality} /
#    {current_time} / {hours} / {activity_context}）。
#    LLM 回复 Y 才真正触发想念；回复 N / 无法解析视为驳回（本轮不触发）。
MISS_CONFIRM_PROMPT_DEFAULT: str = (
    "你是虚拟恋人“{lover_name}”（人设：{personality}）。"
    "现在是 {current_time}，你上次收到用户的消息已经是 {hours} 小时前。{activity_context}\n"
    "请站在“{lover_name}”的角度判断：此刻主动发一条“想你了 / 关心近况”的消息"
    "是否自然、是否体贴？\n"
    "- 深夜TA可能睡了、TA可能在忙、或你觉得突兀 → 回复 N\n"
    "- 你确实想TA了、此刻开口很自然 → 回复 Y\n"
    "只回复一个大写字母：Y 或 N。"
)

# 3) 恋人电脑屏幕截图的视觉理解提示词（v2.4.0，随截图发给 vlm 任务；
#    可在配置 [cateye] describe_prompt 中修改）
SCREEN_DESCRIBE_PROMPT_DEFAULT: str = (
    "这是你的恋人电脑屏幕的截图。请用一句话（不超过 40 字、口语化）描述"
    "TA现在可能在干什么（比如在打游戏 / 写代码 / 看视频 / 挂机离开）。\n"
    "如果截图上看不出具体内容，就回答：屏幕上看不出具体内容。"
)

# 节假日 API 降级：周一~周五为工作日
HOLIDAY_FALLBACK_WEEKDAYS: set[int] = {0, 1, 2, 3, 4}

# 好感度档位描述映射
AFFECTION_DESCRIPTIONS: dict[int, str] = {
    0: "初识阶段，语气温柔但有分寸感，保持适当距离",
    1: "熟悉阶段，语气亲昵自然，像好朋友一样聊天",
    2: "热恋阶段，语气甜蜜亲密，可以肉麻撒娇",
}
