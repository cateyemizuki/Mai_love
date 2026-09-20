"""巡检调度引擎 - 核心模块

v2.0.0: 主动发言从「插件自行调 LLM + send.text」重构为「统一触发 planner」。
- S级：早安/晚安检查 → _trigger_morning / _trigger_night
- A级：想念机制（区间阈值 + LLM 把关）→ _trigger_missing
- B级：日程节点匹配 / 日常巡检 → _trigger_activity / _trigger_daily

所有触发统一走 _trigger_planner → ctx.maisaka.proactive.trigger，
planner 自主决策是否发言、说什么。

v2.3.0: 想念机制改造——
- 触发时长从固定阈值改为可配置区间 [min, max]：每次巡检在区间内随机取阈值，
  沉默越久越容易满足时长条件（平滑爬升而非硬阈值）；
- 触发前可先经 LLM 以角色身份判断"此刻主动说想你了是否自然"，不自然则驳回
  （驳回后 30 分钟内不再重复打扰判断），LLM 不可用/无法解析时按驳回处理；
- 触发 reason 提示词与把关提示词均可在配置中查看和修改。

v2.4.2（cateye 维护）:
- 新增「主动发言最小间隔」（``schedule.min_trigger_interval_minutes``）：对所有主动触发
  （含早安/晚安）生效的硬性间隔，填补此前只能用「用户冷却」凑间隔、而它又被限制在
  60 分钟以内的空白；
- 时间窗口配置非法时不再静默失效：记 warning，且静默时段按「视为静默」、早晚安窗口按
  「不触发」处理（fail-safe，宁可不发）；
- 每轮巡检的判定结论写入「主动行为决策日志」（``decision_logger``），可回答
  "为什么又发了 / 今天怎么没发"。
"""

import asyncio
import random
from datetime import datetime, timedelta
from typing import Any, Optional

from .affection_manager import AffectionManager
from .config import MaiLoverPluginSettings
from .decision_logger import (
    ACTION_INFO,
    ACTION_SKIP,
    ACTION_TRIGGER,
    ProactiveDecisionLogger,
)
from .llm_service import LLMService
from .schedule_generator import ScheduleGenerator

# 想念被 LLM 驳回后，多久之内不再重复发起把关判断（分钟）
MISS_REJECT_COOLDOWN_MINUTES = 30.0

#: 需要校验格式（HH:MM）的时间窗口配置项
TIME_WINDOW_KEYS = (
    "silence_start",
    "silence_end",
    "morning_start",
    "morning_end",
    "night_start",
    "night_end",
)

#: 跳过原因优先级：一轮巡检里若多个候选触发都被挡，只保留优先级最高的那个原因。
#: 否则「早安被最小间隔挡住」会被后面「日常巡检掷点没中」覆盖，日志就答非所问。
#: 数值越大越"值得报告"；同优先级时保留先出现的（即更高等级的触发）。
SKIP_PRIORITY: dict[str, int] = {
    "invalid_time_config": 100,
    "disabled": 96,
    "daily_max_zero": 96,
    "silence": 95,
    "planner_trigger_failed": 85,
    "min_interval": 80,
    "cooldown": 70,
    "budget": 60,
    "llm_rejected": 50,
    "dice": 40,
    "future_schedule": 30,
    "miss_duration": 25,
    "already_sent_today": 10,
    "no_user_message": 10,
    "no_candidate": 0,
}


class Scheduler:
    """巡检调度引擎。

    管理两个后台循环：
    - _daily_generation_loop: 每日凌晨生成日程
    - _patrol_loop: 按配置间隔执行巡检

    v2.0.0: 依赖精简为 4 个（ctx + config + affection + schedule_gen），
    不再直接调 LLM 和 send.text，统一通过 _trigger_planner 触发 planner。
    """

    def __init__(
        self,
        ctx: Any,
        config: MaiLoverPluginSettings,
        affection_manager: AffectionManager,
        schedule_generator: ScheduleGenerator,
        llm_service: Optional[LLMService] = None,
        cateye_client: Optional[Any] = None,
        decision_logger: Optional[ProactiveDecisionLogger] = None,
    ) -> None:
        """初始化调度器。

        Args:
            ctx: MaiBot PluginContext 实例。
            config: 插件强类型配置模型。
            affection_manager: 好感度管理器。
            schedule_generator: 日程生成器。
            llm_service: LLM 服务（想念触发的 LLM 把关用；None = 跳过把关）。
            cateye_client: 恋人电脑联动客户端（None = 不查看电脑状态）。
            decision_logger: 主动行为决策日志（None = 不记录）。
        """
        self._ctx: Any = ctx
        self._config: MaiLoverPluginSettings = config
        self._affection: AffectionManager = affection_manager
        self._schedule_gen: ScheduleGenerator = schedule_generator
        self._llm: Optional[LLMService] = llm_service
        self._cateye: Optional[Any] = cateye_client
        self._decision_logger: Optional[ProactiveDecisionLogger] = decision_logger
        self._stop_event: asyncio.Event = asyncio.Event()
        self._target_qq: str = ""
        self._stream_id: str = ""
        self._personality: str = ""
        self._lover_name: str = "麦麦"
        self._last_trigger_time: Optional[datetime] = None
        self._miss_last_reject_at: Optional[datetime] = None
        # 非法时间窗口配置的告警节流：配置项 -> 已告警过的原始值
        self._invalid_time_warned: dict[str, str] = {}
        # v2.4.3：巡检状态（供 /mai_diag 自诊断）
        self._patrol_task: Optional[asyncio.Task[Any]] = None
        self._last_tick_at: Optional[datetime] = None
        # 最近一次成功触发的意图（供"确认发言"记录标注是哪种触发）
        self._last_trigger_intent: str = ""
        # 上一次记录过的外部日程状态（(result, cached_total)），用于变化时才写日志
        self._logged_external_status: Optional[tuple[str, int]] = None

    def set_target(self, target_qq: str, stream_id: str) -> None:
        """设置白名单目标用户。

        Args:
            target_qq: 目标 QQ 号。
            stream_id: 聊天流 ID。
        """
        self._target_qq = target_qq
        self._stream_id = stream_id

    def set_personality(self, personality: str) -> None:
        """设置恋人的人设性格文本。

        由 plugin 在 on_load 和 on_config_update 时传入，
        用于日程生成时融入人设性格。

        Args:
            personality: 人设性格文本。
        """
        self._personality = personality

    def set_lover_name(self, name: str) -> None:
        """设置恋人名称。

        由 plugin 在 on_load / on_config_update 时传入，
        替代默认的"麦麦"。读取自 bot.nickname 配置。

        Args:
            name: 恋人名称。
        """
        if name:
            self._lover_name = name

    def get_last_trigger_time(self) -> Optional[datetime]:
        """返回上次成功 proactive trigger 的时间。"""
        return self._last_trigger_time

    def get_last_trigger_intent(self) -> str:
        """返回上次成功 proactive trigger 的意图（morning/night/missing/daily/activity）。"""

        return self._last_trigger_intent

    def clear_last_trigger_time(self) -> None:
        """清除上次触发时间与意图（replyer Hook 补计后调用）。"""
        self._last_trigger_time = None
        self._last_trigger_intent = ""

    @property
    def is_patrolling(self) -> bool:
        """巡检循环是否在运行（供 /mai_diag 自诊断：为空时区分"没启动"与"刚启动"）。"""

        task = self._patrol_task
        return bool(task is not None and not task.done())

    def patrol_status(self) -> dict[str, Any]:
        """返回巡检运行状态快照（/mai_diag 汇总行使用）。"""

        return {
            "running": self.is_patrolling,
            "last_tick_at": (
                self._last_tick_at.strftime("%Y-%m-%d %H:%M:%S") if self._last_tick_at else ""
            ),
            "interval_minutes": int(getattr(self._config.schedule, "check_interval_minutes", 0) or 0),
            "stream_id_ready": bool(self._stream_id),
            "trigger_enabled": bool(
                getattr(self._config.schedule, "proactive_trigger_enabled", True)
            ),
            "external_schedule": self._use_external_schedule(),
        }

    async def start(self) -> None:
        """启动调度引擎。

        日程生成循环不依赖 stream_id，始终启动。
        巡检循环需要 stream_id 才能触发 proactive trigger，无 stream_id 时跳过。

        外部日程模式（use_external_schedule）：清空本地日程缓存、不再启动
        日程生成循环，日程改由巡检时从自主规划插件拉取（_tick 内刷新）。
        """
        self._stop_event.clear()  # 重置停止标志，支持 stop() 后重新 start()
        self._ctx.logger.info(f"Scheduler 启动，目标用户: {self._target_qq or '(未设置)'}")

        # 检查是否需要重置每日计数
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        if self._affection.today_date() != today_str:
            self._affection.reset_daily(today_str)

        if self._use_external_schedule():
            # 外部日程模式：清空已有日程，往后不再生成（幂等，重复启动无害）
            self._schedule_gen.clear_cached_schedule()
            self._ctx.logger.info(
                "外部日程模式已开启：本地日程已清空，不再生成日程，"
                "改为巡检时读取自主规划插件日程"
            )
        else:
            # 首次启动或日程缺失时立即生成今日日程
            # 优先检查标记文件（防竞态），回退检查缓存文件
            if not self._schedule_gen.is_generated_today(today_str):
                self._ctx.logger.info("今日无日程缓存，立即生成")
                try:
                    await self._schedule_gen.generate_daily_schedule(
                        today_str, self._personality, self._lover_name
                    )
                except Exception as e:
                    self._ctx.logger.error(f"立即生成日程失败: {e}")

            # 日程生成循环始终启动（不依赖 stream_id）
            asyncio.create_task(self._daily_generation_loop())

        # 巡检循环需要 stream_id
        if self._stream_id:
            self._patrol_task = asyncio.create_task(self._patrol_loop())
            self._ctx.logger.info("巡检循环已启动")
        else:
            self._ctx.logger.warning("无 stream_id，巡检循环未启动（日程生成不受影响）")

    def _use_external_schedule(self) -> bool:
        """是否使用外部日程（自主规划插件）。配置缺字段时安全回退 False。"""
        return bool(getattr(self._config.schedule, "use_external_schedule", False))

    async def start_patrol(self) -> None:
        """单独启动巡检循环（用于 stream_id 延迟获取后补启）。"""
        if not self._stream_id:
            self._ctx.logger.warning("无 stream_id，无法启动巡检循环")
            return
        self._patrol_task = asyncio.create_task(self._patrol_loop())
        self._ctx.logger.info("巡检循环已启动（延迟补启）")

    async def _daily_generation_loop(self) -> None:
        """每日凌晨唤醒，生成当日日程。

        在 _stop_event 上等待到下一个 generate_hour 时刻，
        然后生成日程、重置每日计数，循环往复。

        外部日程模式下不该被启动；此处再守一道，防止误启动后生成日程。
        """
        if self._use_external_schedule():
            self._ctx.logger.debug("外部日程模式，日程生成循环未启动")
            return

        generate_hour = self._config.schedule.generate_hour

        while not self._stop_event.is_set():
            now = datetime.now()
            next_run = now.replace(
                hour=generate_hour, minute=0, second=0, microsecond=0
            )
            if now >= next_run:
                # 已过今天凌晨，等到明天凌晨
                next_run = next_run + timedelta(days=1)

            wait_seconds = (next_run - now).total_seconds()
            self._ctx.logger.debug(
                f"下次日程生成时间: {next_run}，等待 {wait_seconds:.0f} 秒"
            )

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=max(wait_seconds, 1),
                )
                # 如果 wait 被 set 触发（非超时），说明是 stop 信号
                if self._stop_event.is_set():
                    break
            except asyncio.TimeoutError:
                # 超时 = 到达生成时间
                pass

            if self._stop_event.is_set():
                break

            # 执行日程生成
            date_str = datetime.now().strftime("%Y-%m-%d")
            self._ctx.logger.info(f"开始生成 {date_str} 的日程")
            try:
                await self._schedule_gen.generate_daily_schedule(
                    date_str, self._personality, self._lover_name
                )
            except Exception as e:
                self._ctx.logger.error(f"日程生成失败: {e}")

            # 日切由 _tick 中 today_date 检测统一负责，此处不再 reset
            # 避免 generate_hour 落在白天时覆盖已发送的早安/晚安状态
            if self._affection.today_date() != date_str:
                self._affection.reset_daily(date_str)

    async def _patrol_loop(self) -> None:
        """巡检循环。

        按 check_interval_minutes 间隔执行 _tick()。
        """
        check_interval = self._config.schedule.check_interval_minutes
        self._ctx.logger.info(f"巡检循环启动，间隔 {check_interval} 分钟")

        while not self._stop_event.is_set():
            try:
                await self._tick()
            except Exception as e:
                self._ctx.logger.error(f"巡检 _tick 异常: {e}")

            # 等待下一次巡检
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=check_interval * 60,
                )
                # 如果 wait 被 set 触发（非超时），说明是 stop 信号
                if self._stop_event.is_set():
                    break
            except asyncio.TimeoutError:
                # 超时 = 下一轮巡检
                continue

    async def _tick(self) -> None:
        """单次巡检，严格按优先级执行。

        优先级顺序：
        1. S级：早安/晚安检查（强制性，跳过概率和冷却检查）
        2. A级：想念机制（6 条件）
        3. B级：日程节点匹配 / 日常巡检（概率 + 冷却 + 上限）

        所有触发统一走 _trigger_planner → ctx.maisaka.proactive.trigger，
        planner 自主决策是否发言、说什么。所有时间相关操作复用同一个 now。
        """
        now = datetime.now()
        current_time = now.strftime("%H:%M")
        current_date = now.strftime("%Y-%m-%d")
        self._last_tick_at = now

        # 确保当日数据已重置
        if self._affection.today_date() != current_date:
            self._affection.reset_daily(current_date)

        windows = self._config.time_windows
        schedule_cfg = self._config.schedule
        silence_start = windows.silence_start
        silence_end = windows.silence_end
        morning_start = windows.morning_start
        morning_end = windows.morning_end
        night_start = windows.night_start
        night_end = windows.night_end

        daily_max_speak = int(schedule_cfg.daily_max_speak)

        # 为晚安预留 1 个配额：晚安未发时，非晚安触发只能用 daily_max_speak-1
        if self._affection.night_sent_today():
            non_night_budget = daily_max_speak  # 晚安已发，释放预留
        else:
            non_night_budget = daily_max_speak - 1  # 为晚安预留

        # ---- v2.4.2：主动发言最小间隔 / 时间配置校验 / 决策日志 ----
        minutes_since_last_speak = self._minutes_since_last_speak(now)
        min_interval = max(0, int(getattr(schedule_cfg, "min_trigger_interval_minutes", 0)))
        exempt_greetings = bool(getattr(schedule_cfg, "min_interval_exempt_greetings", False))
        interval_blocked = (
            min_interval > 0
            and minutes_since_last_speak is not None
            and minutes_since_last_speak < min_interval
        )
        interval_desc = (
            f"距上次主动发言 {minutes_since_last_speak:.0f} 分钟 < 最小间隔 {min_interval} 分钟"
            if interval_blocked
            else ""
        )
        invalid_time_keys = self._collect_invalid_time_keys()

        # 本轮判定（每轮恰好写一条；一旦真的发言，后续门控的"跳过"不再覆盖它）
        decision: dict[str, str] = {
            "action": ACTION_SKIP,
            "reason": "no_candidate",
            "trigger_type": "",
            "detail": "本轮没有满足条件的触发",
        }
        base_fields: dict[str, Any] = {
            "minutes_since_last_speak": (
                None if minutes_since_last_speak is None else round(minutes_since_last_speak, 1)
            ),
            "min_interval_minutes": min_interval,
            "cooldown_minutes": int(schedule_cfg.user_cooldown_minutes),
            "budget_used": round(self._affection.today_speak_count(), 2),
            "budget_limit": non_night_budget,
        }
        if invalid_time_keys:
            base_fields["invalid_time_config"] = ",".join(invalid_time_keys)

        def mark_skip(reason: str, trigger_type: str = "", detail: str = "") -> None:
            """记录"本轮跳过"。

            本轮已经发言时不再改写；多个候选都被挡时只保留优先级更高的原因
            （同优先级保留先出现的，即更高等级的触发），避免"早安被最小间隔挡住"
            被后面的"日常巡检掷点没中"覆盖。
            """

            if decision["action"] == ACTION_TRIGGER:
                return
            if decision["reason"] != "no_candidate" and (
                SKIP_PRIORITY.get(reason, 0) <= SKIP_PRIORITY.get(decision["reason"], 0)
            ):
                return
            decision.update(
                action=ACTION_SKIP, reason=reason, trigger_type=trigger_type, detail=detail
            )

        def mark_trigger(trigger_type: str, detail: str = "") -> None:
            """记录"本轮真的发言了"。"""

            decision.update(
                action=ACTION_TRIGGER,
                reason=f"{trigger_type}_trigger",
                trigger_type=trigger_type,
                detail=detail,
            )

        def finish() -> None:
            """把本轮判定写入主动行为决策日志。"""

            self._log_decision(**decision, **base_fields)

        # 总开关关闭 / 每日上限为 0 → 直接停（也记一条，便于解释"今天完全没动静"）
        if not schedule_cfg.proactive_trigger_enabled:
            mark_skip("disabled", detail="主动触发开关（proactive_trigger_enabled）已关闭")
            finish()
            return
        if daily_max_speak <= 0:
            mark_skip("daily_max_zero", detail="每日发言上限为 0（完全静音）")
            finish()
            return

        # 静默时段：完全不触发任何主动行为。
        # 时间配置非法时按"视为静默"处理（fail-safe，宁可不发），_collect_invalid_time_keys 已告警
        if self._is_in_time_window(silence_start, silence_end, current_time, invalid_result=True):
            if invalid_time_keys:
                mark_skip(
                    "invalid_time_config",
                    detail=f"时间窗口配置非法（{','.join(invalid_time_keys)}），按静默处理（fail-safe）",
                )
            else:
                mark_skip("silence", detail=f"处于静默时段 {silence_start}-{silence_end}")
            finish()
            return

        # ==============================
        # S级：早安检查（无视概率与「用户冷却」，但受静默/最小间隔/每日上限约束）
        # ==============================
        if self._is_in_time_window(morning_start, morning_end, current_time):
            if self._affection.morning_sent_today():
                mark_skip("already_sent_today", "morning", "今天已经发过早安")
            elif interval_blocked and not exempt_greetings:
                mark_skip("min_interval", "morning", interval_desc)
            elif self._affection.today_speak_count() >= non_night_budget:
                mark_skip(
                    "budget",
                    "morning",
                    f"当日预算已用 {self._affection.today_speak_count():.1f}/{non_night_budget}（未发晚安，为晚安预留 1 条）",
                )
            elif await self._trigger_morning():
                mark_trigger("morning", "早安触发已入队，由 planner 决定是否发言")
            else:
                mark_skip("planner_trigger_failed", "morning", "触发未入队（stream_id 缺失或触发开关关闭）")

        # ==============================
        # S级：晚安检查
        # ==============================
        if self._is_in_time_window(night_start, night_end, current_time):
            if self._affection.night_sent_today():
                mark_skip("already_sent_today", "night", "今天已经发过晚安")
            elif interval_blocked and not exempt_greetings:
                mark_skip("min_interval", "night", interval_desc)
            elif self._affection.today_speak_count() >= daily_max_speak:
                mark_skip(
                    "budget",
                    "night",
                    f"当日预算已用 {self._affection.today_speak_count():.1f}/{daily_max_speak}",
                )
            elif await self._trigger_night():
                mark_trigger("night", "晚安触发已入队，由 planner 决定是否发言")
            else:
                mark_skip("planner_trigger_failed", "night", "触发未入队（stream_id 缺失或触发开关关闭）")

        # ==============================
        # A级：想念机制
        # ==============================
        # 时长条件：在配置区间 [min, max] 内随机取阈值，沉默时间超过阈值即满足
        # ——沉默越久越容易触发（平滑爬升），min 之前绝不触发。
        miss_speak_rate = self._config.probability.miss_speak_rate

        if self._affection.miss_sent_today():
            mark_skip("already_sent_today", "miss", "今天已经发过想念")
        else:
            last_msg = self._affection.last_user_msg_time()
            if last_msg is None:
                # 用户从未发过消息 → 不触发想念（避免首次启动就喊想你了）
                mark_skip("no_user_message", "miss", "还没有用户消息记录，不触发想念")
            else:
                hours_since_last = (now - last_msg).total_seconds() / 3600
                miss_threshold = self._sample_miss_threshold()
                if hours_since_last <= miss_threshold:
                    mark_skip(
                        "miss_duration",
                        "miss",
                        f"用户沉默 {hours_since_last:.1f}h ≤ 本轮随机阈值 {miss_threshold:.1f}h"
                        f"（区间 {self._config.time_windows.miss_trigger_hours_min}"
                        f"~{self._config.time_windows.miss_trigger_hours_max}h）",
                    )
                elif self._has_future_schedule(2, now):
                    mark_skip("future_schedule", "miss", "未来 2 小时内有日程节点，先不打扰")
                elif self._affection.today_speak_count() >= non_night_budget:
                    mark_skip(
                        "budget",
                        "miss",
                        f"当日预算已用 {self._affection.today_speak_count():.1f}/{non_night_budget}",
                    )
                elif interval_blocked:
                    mark_skip("min_interval", "miss", interval_desc)
                elif self._is_in_cooldown(now):
                    mark_skip(
                        "cooldown",
                        "miss",
                        f"处于用户冷却期（{int(schedule_cfg.user_cooldown_minutes)} 分钟）内",
                    )
                elif random.random() >= miss_speak_rate:
                    mark_skip("dice", "miss", f"想念概率未通过（miss_speak_rate={miss_speak_rate}）")
                else:
                    # 电脑状态本次巡检只取一份：把关与触发复用，
                    # 避免一次触发连截两张屏
                    computer_context = await self._get_computer_context(now)
                    # LLM 把关：以角色身份判断此刻开口是否自然，
                    # 驳回则本轮不触发（30 分钟冷却内不重复判断）
                    if self._miss_llm_check_enabled() and not await self._confirm_missing_with_llm(
                        hours_since_last, now, computer_context
                    ):
                        mark_skip("llm_rejected", "miss", "LLM 把关驳回（30 分钟内不重复判断）")
                    elif await self._trigger_missing(hours_since_last, computer_context):
                        mark_trigger("miss", f"想念触发已入队（用户沉默 {hours_since_last:.1f}h）")
                    else:
                        mark_skip(
                            "planner_trigger_failed",
                            "miss",
                            "触发未入队（stream_id 缺失或触发开关关闭）",
                        )

        # ==============================
        # B级：日程节点匹配 / 日常巡检
        # ==============================
        activity_trigger_rate = self._config.probability.activity_trigger_rate
        default_speak_rate = self._config.probability.default_speak_rate

        # 外部日程模式：巡检时刷新（内部带 TTL 节流；非外部模式为 no-op）。
        # v2.4.3：把拉取结果记进决策日志——外部日程模式下插件不生成日程，
        # 若这里读不到节点，「日程节点分享」就永远不会触发，必须有据可查
        external_status = await self._schedule_gen.refresh_external_schedule(current_date)
        self._log_external_schedule_status(external_status)

        schedule = self._schedule_gen.load_cached_schedule(current_date)
        node_matched = False
        for node in schedule:
            node_time = str(node.get("time", ""))
            if not node_time:
                continue
            if self._time_match(node_time, current_time):
                node_matched = True
                # 节点匹配 → activity_trigger_rate 概率触发活动分享
                if interval_blocked:
                    mark_skip("min_interval", "activity", interval_desc)
                elif self._is_in_cooldown(now):
                    mark_skip(
                        "cooldown",
                        "activity",
                        f"处于用户冷却期（{int(schedule_cfg.user_cooldown_minutes)} 分钟）内",
                    )
                elif self._affection.today_speak_count() >= non_night_budget:
                    mark_skip(
                        "budget",
                        "activity",
                        f"当日预算已用 {self._affection.today_speak_count():.1f}/{non_night_budget}",
                    )
                elif random.random() >= activity_trigger_rate:
                    mark_skip(
                        "dice",
                        "activity",
                        f"活动分享概率未通过（activity_trigger_rate={activity_trigger_rate}）",
                    )
                elif await self._trigger_activity(node):
                    node_label = str(node.get("activity") or node.get("name") or node_time)
                    mark_trigger("activity", f"日程节点触发已入队：{node_time} {node_label}")
                else:
                    mark_skip(
                        "planner_trigger_failed",
                        "activity",
                        "触发未入队（stream_id 缺失或触发开关关闭）",
                    )
                break  # 只匹配一个节点

        # 没有节点匹配 → default_speak_rate 概率触发日常巡检
        if not node_matched:
            if interval_blocked:
                mark_skip("min_interval", "daily", interval_desc)
            elif self._is_in_cooldown(now):
                mark_skip(
                    "cooldown",
                    "daily",
                    f"处于用户冷却期（{int(schedule_cfg.user_cooldown_minutes)} 分钟）内",
                )
            elif self._affection.today_speak_count() >= non_night_budget:
                mark_skip(
                    "budget",
                    "daily",
                    f"当日预算已用 {self._affection.today_speak_count():.1f}/{non_night_budget}",
                )
            elif random.random() >= default_speak_rate:
                mark_skip(
                    "dice",
                    "daily",
                    f"日常巡检概率未通过（default_speak_rate={default_speak_rate}）",
                )
            elif await self._trigger_daily():
                mark_trigger("daily", "日常巡检触发已入队")
            else:
                mark_skip(
                    "planner_trigger_failed",
                    "daily",
                    "触发未入队（stream_id 缺失或触发开关关闭）",
                )

        finish()

    async def _trigger_planner(self, intent: str, reason: str) -> bool:
        """统一触发 planner 主动处理。

        Args:
            intent: 触发意图（"morning"/"night"/"missing"/"daily"/"activity"）
            reason: 传给 planner 的提示文本

        Returns:
            True 表示触发成功（trigger 入队成功），False 表示失败或未触发
        """
        if not self._stream_id:
            return False
        if not self._config.schedule.proactive_trigger_enabled:
            return False
        try:
            await self._ctx.maisaka.proactive.trigger(
                stream_id=self._stream_id,
                intent=intent,
                reason=reason,
            )
            self._affection.increment_speak(0.5)  # 先计 0.5，replyer 回复后再补 0.5
            self._last_trigger_time = datetime.now()
            self._last_trigger_intent = str(intent or "")
            return True
        except Exception as e:
            self._ctx.logger.error(f"proactive_trigger 失败: {e}")
            return False

    async def _trigger_morning(self) -> bool:
        """触发早安 planner（触发前看一眼恋人的电脑）；返回是否入队成功。"""
        self._ctx.logger.info("S级触发: 早安")
        reason = "早上好，可以说早安" + await self._get_computer_context(datetime.now())
        success = await self._trigger_planner("morning", reason)
        if success:
            self._affection.set_morning_sent()
        return success

    async def _trigger_night(self) -> bool:
        """触发晚安 planner（触发前看一眼恋人的电脑）；返回是否入队成功。"""
        self._ctx.logger.info("S级触发: 晚安")
        reason = "晚上好，可以说晚安" + await self._get_computer_context(datetime.now())
        success = await self._trigger_planner("night", reason)
        if success:
            self._affection.set_night_sent()
        return success

    async def _trigger_missing(
        self, hours_since_last: float, computer_context: str = ""
    ) -> bool:
        """触发想念 planner；返回是否入队成功。

        Args:
            hours_since_last: 距用户最后一条消息的小时数（填入 reason 提示词）。
            computer_context: 恋人电脑状态文案（把关与触发复用同一份，避免重复截图）。
        """
        self._ctx.logger.info(
            f"A级触发: 想念机制（沉默 {hours_since_last:.1f} 小时）"
        )
        reason = self._format_miss_reason(hours_since_last) + computer_context
        success = await self._trigger_planner("missing", reason)
        if success:
            self._affection.set_miss_sent()
        return success

    async def _get_computer_context(self, now: datetime) -> str:
        """获取恋人电脑状态文案（未启用联动 / 无客户端时返回空串）。"""
        if self._cateye is None or not self._cateye.is_enabled():
            return ""
        return await self._cateye.get_computer_context(now)

    # ── 想念机制辅助（v2.3.0）────────────────────────────────────────

    @staticmethod
    def _resolve_miss_window(low: Any, high: Any) -> tuple[float, float]:
        """解析并排序想念触发区间，保证 (下限 ≤ 上限)。

        兼容配置缺字段（getattr 默认值）与写反的情况。
        """
        try:
            low_f = float(low)
        except (TypeError, ValueError):
            low_f = 4.0
        try:
            high_f = float(high)
        except (TypeError, ValueError):
            high_f = 8.0
        return (min(low_f, high_f), max(low_f, high_f))

    def _sample_miss_threshold(self) -> float:
        """在配置区间内随机取本次巡检的想念触发阈值（小时）。

        每次巡检独立抽样：沉默时间超过阈值才满足时长条件，
        因此触发概率随沉默时长在 [min, max] 间平滑爬升。
        """
        tw = getattr(self._config, "time_windows", None)
        low, high = self._resolve_miss_window(
            getattr(tw, "miss_trigger_hours_min", 4.0),
            getattr(tw, "miss_trigger_hours_max", 8.0),
        )
        return random.uniform(low, high)

    def _miss_llm_check_enabled(self) -> bool:
        """是否启用想念触发前的 LLM 把关（配置缺字段时安全回退 False）。"""
        if self._llm is None:
            return False
        tw = getattr(self._config, "time_windows", None)
        return bool(getattr(tw, "miss_llm_check_enabled", False))

    def _format_miss_reason(self, hours_since_last: float) -> str:
        """格式化想念触发的 reason 提示词（模板可在配置中修改）。

        用户改坏占位符或格式化失败时回退为纯文本，保证触发不受影响。
        """
        template = getattr(
            getattr(self._config, "time_windows", None),
            "miss_reason_prompt",
            "",
        )
        try:
            return template.format(
                lover_name=self._lover_name,
                hours=f"{hours_since_last:.1f}",
            )
        except Exception:  # noqa: BLE001（KeyError/IndexError/ValueError 等）
            return (
                f"你已经有 {hours_since_last:.1f} 个小时没收到用户的消息了，"
                "可以考虑主动找TA聊聊，注意自然贴合当前情境。"
            )

    async def _confirm_missing_with_llm(
        self,
        hours_since_last: float,
        now: datetime,
        computer_context: str = "",
    ) -> bool:
        """想念触发前的 LLM 把关：此刻主动表达想念是否自然。

        用配置的 ``miss_confirm_prompt`` 模板构造提示词（含沉默时长、当前
        时间、当前活动与恋人电脑状态），LLM 回复 Y 才放行；N、无法解析或
        调用失败一律视为驳回——宁可这轮不打扰，也不硬接话题。

        Args:
            hours_since_last: 距用户最后一条消息的小时数。
            now: 当前时间（复用 _tick 的 now）。
            computer_context: 恋人电脑状态文案（"" = 未启用联动）。

        Returns:
            True = 放行触发；False = 驳回（新鲜驳回时记录驳回时间，
            之后 30 分钟冷却内的短路判断不再刷新时间戳，保证可恢复）。
        """
        # 30 分钟内刚被驳回过：不重复打扰 LLM 判断，直接视为驳回
        if self._miss_recently_rejected(now):
            return False

        tw = getattr(self._config, "time_windows", None)
        template = getattr(tw, "miss_confirm_prompt", "") or ""
        activity = self._schedule_gen.find_current_activity(now)
        activity_context = (
            f"你当前的活动：{activity}。" if activity else "你现在没有安排中的活动。"
        )
        try:
            prompt = template.format(
                lover_name=self._lover_name,
                personality=self._personality or "（未配置人设）",
                current_time=now.strftime("%H:%M"),
                hours=f"{hours_since_last:.1f}",
                activity_context=activity_context,
            )
        except Exception:  # noqa: BLE001（占位符被改坏时回退默认模板）
            from .constants import MISS_CONFIRM_PROMPT_DEFAULT

            prompt = MISS_CONFIRM_PROMPT_DEFAULT.format(
                lover_name=self._lover_name,
                personality=self._personality or "（未配置人设）",
                current_time=now.strftime("%H:%M"),
                hours=f"{hours_since_last:.1f}",
                activity_context=activity_context,
            )
        if computer_context:
            prompt += f"\n{computer_context}"

        assert self._llm is not None  # _miss_llm_check_enabled 已保证
        response = await self._llm.generate(
            prompt=prompt, temperature=0.2, max_tokens=16, event="miss_confirm"
        )
        verdict = self._parse_missing_confirm(response)
        if verdict is True:
            self._ctx.logger.info("想念把关：LLM 判定此刻开口自然，放行触发")
            return True
        self._miss_last_reject_at = now  # 只在新鲜驳回时记录，冷却可自然过期
        self._ctx.logger.info(
            "想念把关：LLM 驳回本轮触发"
            f"（response={response!r}，{MISS_REJECT_COOLDOWN_MINUTES:.0f} 分钟后才会再次判断）"
        )
        return False

    def _miss_recently_rejected(self, now: datetime) -> bool:
        """是否处于上次 LLM 驳回后的冷却期。"""
        if self._miss_last_reject_at is None:
            return False
        elapsed = (now - self._miss_last_reject_at).total_seconds() / 60
        return elapsed < MISS_REJECT_COOLDOWN_MINUTES

    @staticmethod
    def _parse_missing_confirm(response: str) -> Optional[bool]:
        """解析 LLM 把关回复：Y=放行，N/空/无法解析=驳回（None 表示无法解析）。

        Returns:
            True / False / None（无法判定；调用方按驳回处理）。
        """
        text = str(response or "").strip()
        if not text:
            return None
        upper = text.upper()
        for ch in upper:
            if ch == "Y":
                return True
            if ch == "N":
                return False
        # 中文字样兜底（LLM 无视"只回一个字母"的要求时）
        if any(kw in text for kw in ("不", "否", "拒绝", "驳回")):
            return False
        if any(kw in text for kw in ("是", "可以", "自然", "合适")):
            return True
        return None

    async def _trigger_activity(self, node: dict[str, Any]) -> bool:
        """触发日程节点活动 planner；返回是否入队成功。

        Args:
            node: 日程节点（含 activity 字段）。
        """
        activity = str(node.get("activity", ""))
        self._ctx.logger.info(f"B级触发: 日程节点 - {activity}")
        return await self._trigger_planner(
            "activity", f"{self._lover_name}现在在{activity}，可以分享"
        )

    async def _trigger_daily(self) -> bool:
        """触发日常巡检 planner；返回是否入队成功。"""
        self._ctx.logger.info("B级触发: 日常巡检")
        return await self._trigger_planner("daily", "日常巡检")

    def _minutes_since_last_speak(self, now: datetime) -> Optional[float]:
        """距上一次主动发言过去了多少分钟；没有记录时返回 None。"""

        last_speak = self._affection.last_speak_time()
        if last_speak is None:
            return None
        return max(0.0, (now - last_speak).total_seconds() / 60)

    def _collect_invalid_time_keys(self) -> list[str]:
        """收集格式非法的（HH:MM）时间窗口配置项，并对每个非法值告警一次。

        非法值会按 fail-safe 处理：静默时段视为"在静默中"（不发言），
        早晚安窗口视为"不在窗口内"（不触发）。
        """

        invalid: list[str] = []
        for key in TIME_WINDOW_KEYS:
            raw = str(getattr(self._config.time_windows, key, "") or "")
            try:
                Scheduler._time_to_minutes(raw)
            except ValueError:
                invalid.append(key)
                if self._invalid_time_warned.get(key) != raw:
                    self._invalid_time_warned[key] = raw
                    level = getattr(self._ctx.logger, "warning", self._ctx.logger.info)
                    level(
                        f"时间窗口配置 {key}={raw!r} 不是 HH:MM 格式；已按 fail-safe 处理"
                        f"（静默时段视为静默、早晚安窗口视为不触发，宁可不发）。请检查插件配置。"
                    )
        return invalid

    def _log_decision(self, *, action: str, **fields: Any) -> None:
        """写一条主动行为决策记录（未启用日志时静默 no-op）。"""

        if self._decision_logger is None:
            return
        try:
            self._decision_logger.record(action=action, **fields)
        except Exception as e:  # noqa: BLE001 - 日志故障绝不影响巡检
            self._ctx.logger.debug(f"写主动行为决策日志失败（忽略）: {e}")

    def _log_external_schedule_status(self, status: Any) -> None:
        """把外部日程拉取结果记进决策日志（v2.4.3）。

        只在状态/节点数发生变化时记录。``fresh`` 与 ``cached`` 都表示"读到了"，
        必须归并成同一种状态——否则 2 分钟 TTL + 10 分钟巡检会让它们每轮交替出现，
        变成 144 行/天的噪声。``error`` / ``exception`` / ``empty`` 每次都记：
        这三种才是"为什么一直没有日程节点分享"的答案。
        """

        if not isinstance(status, dict) or status.get("mode") != "external":
            return
        result = str(status.get("result") or "unknown")
        total = int(status.get("cached_total") or 0)
        healthy = result in {"fresh", "cached"}
        key = ("ok" if healthy else result, total)
        if key == self._logged_external_status and healthy:
            return
        self._logged_external_status = key
        self._log_decision(
            action=ACTION_INFO,
            reason=f"external_schedule_{result}",
            trigger_type="schedule_source",
            detail=str(status.get("detail") or ""),
            schedule_mode="external",
            schedule_nodes=total,
            schedule_fetched=int(status.get("nodes") or 0),
        )

    def _is_in_cooldown(self, now: datetime) -> bool:
        """检查是否在冷却期内。

        Args:
            now: 当前时间（复用 _tick 的 now）。

        Returns:
            True 表示在冷却期内。
        """
        cooldown_minutes = self._config.schedule.user_cooldown_minutes
        recent_times = [
            value
            for value in (
                self._affection.last_speak_time(),
                self._affection.last_user_msg_time(),
            )
            if value is not None
        ]
        if not recent_times:
            return False
        elapsed = (now - max(recent_times)).total_seconds() / 60
        return elapsed < cooldown_minutes

    @staticmethod
    def _is_in_time_window(
        window_start: str, window_end: str, current: str, *, invalid_result: bool = False
    ) -> bool:
        """检查当前时间是否在指定时间窗口内。

        处理跨日窗口的情况（如 22:00 ~ 02:00）。

        Args:
            window_start: 窗口开始时间（HH:MM）。
            window_end: 窗口结束时间（HH:MM）。
            current: 当前时间（HH:MM）。
            invalid_result: 时间字符串解析失败时的返回值（fail-safe 方向）。
                静默时段传 True（"视为静默"，宁可不发）；早晚安窗口保持默认 False
                （"不在窗口内"，不触发）。

        Returns:
            True 表示在窗口内（或解析失败且 invalid_result=True）。
        """
        try:
            start_minutes = Scheduler._time_to_minutes(window_start)
            end_minutes = Scheduler._time_to_minutes(window_end)
            current_minutes = Scheduler._time_to_minutes(current)
        except ValueError:
            return invalid_result

        if start_minutes <= end_minutes:
            return start_minutes <= current_minutes <= end_minutes
        return current_minutes >= start_minutes or current_minutes <= end_minutes

    @staticmethod
    def _time_to_minutes(value: str) -> int:
        """将 H:MM/HH:MM 转为当日分钟数，避免字符串比较误判。"""
        parsed = datetime.strptime(value, "%H:%M")
        return parsed.hour * 60 + parsed.minute

    @staticmethod
    def _time_match(node_time: str, current: str) -> bool:
        """检查节点时间是否匹配当前时间（±1分钟容差）。

        Args:
            node_time: 节点时间（HH:MM）。
            current: 当前时间（HH:MM）。

        Returns:
            True 表示匹配。
        """
        try:
            node_dt = datetime.strptime(node_time, "%H:%M")
            curr_dt = datetime.strptime(current, "%H:%M")
            diff_seconds = abs((curr_dt - node_dt).total_seconds())
            return diff_seconds <= 60  # ±1 分钟
        except ValueError:
            return False

    def _has_future_schedule(self, hours: int, now: datetime) -> bool:
        """检查未来指定小时内是否有日程节点。

        Args:
            hours: 未来时间窗口（小时数）。
            now: 当前时间（复用 _tick 的 now）。

        Returns:
            True 表示有日程节点。
        """
        current_date = now.strftime("%Y-%m-%d")
        schedule = self._schedule_gen.load_cached_schedule(current_date)

        if not schedule:
            return False

        future_limit = now + timedelta(hours=hours)

        for node in schedule:
            node_time = str(node.get("time", ""))
            if not node_time:
                continue
            try:
                node_dt = datetime.strptime(
                    f"{current_date} {node_time}", "%Y-%m-%d %H:%M"
                )
                if now < node_dt <= future_limit:
                    return True
            except ValueError:
                continue

        return False

    def stop(self) -> None:
        """停止所有协程。"""
        self._ctx.logger.info("Scheduler 收到停止信号")
        self._stop_event.set()
