"""麦麦恋人（MaiLover）插件入口

MaiBot 私聊专用虚拟恋人插件（NapCat 适配器）。
核心逻辑：日程定骨架 + 概率定节奏 + 情绪定温度。

v2.0.0 架构变更：
- 主动发言统一触发 planner（ctx.maisaka.proactive.trigger），
  不再自行调 LLM + send.text
- 新增 planner.before_request Hook，注入麦麦当前活动状态
- 新增 mai_lover_current_activity Tool，供 planner 查询麦麦在干嘛
- 人设缓存：on_load 时读取 ctx.config.get("personality.personality")，
  on_config_update(scope="bot") 时刷新
- 删除关键词拦截（hook_handler.py），消息正常放行给 planner

导出 create_plugin() 函数返回 MaiLoverPlugin 实例。
"""

import asyncio
import os
from datetime import datetime
from typing import Any, ClassVar, Iterable, Optional
from uuid import uuid4

from maibot_sdk import API, Command, HookHandler, MaiBotPlugin, Tool
from maibot_sdk.types import ErrorPolicy, HookMode, HookOrder

from .affection_manager import AffectionManager
from .cateye_client import CateyeClient
from .config import MaiLoverPluginSettings
from .constants import AFFECTION_DESCRIPTIONS
from .decision_logger import (
    ACTION_INFO,
    ACTION_SKIP,
    ACTION_SPOKEN,
    ACTION_TRIGGER,
    ProactiveDecisionLogger,
)
from .external_schedule import ExternalScheduleSource
from .holiday_service import HolidayService
from .llm_logger import LLMCallLogger
from .llm_service import LLMService
from .memory_manager import MemoryManager
from .message_service import MessageService
from .schedule_generator import ScheduleGenerator
from .scheduler import Scheduler

# ──────────────────────────────────────────────────────────────────────────
# [LOCAL-PATCH:cateye] 本地修改（基于上游 v2.4.0），共两处，均已用该标记注释：
#   1) planner 活动注入的 payload 守卫：原先 payload > 1MB 就整体跳过注入，而宿主把图片
#      以 image_base64 内联进 items 快照（request_snapshot.py:264-271），带几张图的上下文
#      常态就是 2–12MB —— 线上日志实测注入几乎从未生效。现改为"超限则按体积从大到小把
#      图片 part 换成文本占位符后照常注入"。
#   2) 主动发言回合注入「回复目标约束」：宿主 maisaka.proactive.trigger 能力只有
#      stream_id/intent/reason/priority/metadata（capabilities/core.py:219-250），**没有
#      任何回复目标参数**，回复对象完全由 planner 自主决定；主动私聊那一轮没有用户消息
#      可锚，目标就会落到 bot 自己上一条发言。这里用一条软约束把目标引导回对方，只在最近
#      _PROACTIVE_RULE_WINDOW_SECONDS 秒内有过 proactive trigger 时注入，不影响普通回合。
# ──────────────────────────────────────────────────────────────────────────
#: 超过此体积就先省略图片再注入；宿主单帧上限 16MB（transport/base.py:18），留编码余量。
_MAX_INJECT_PAYLOAD_BYTES = 8 * 1024 * 1024
#: 距离上次 proactive trigger 多久内，认为当前 planner 回合是主动发言回合。
_PROACTIVE_RULE_WINDOW_SECONDS = 90.0
#: 省略图片时替换成的文本占位符（不能直接删 part，否则 item 可能没有 part）。
_IMAGE_OMITTED_PLACEHOLDER = "[图片已省略：为控制上下文体积]"

#: 主动行为决策日志里的跳过原因 → 中文说明（/mai_diag 展示用）。
#: 原因键由 ``Scheduler._tick`` 写入；这里只做人类可读映射，未知键会原样显示。
_DIAG_REASON_LABELS: dict[str, str] = {
    "no_candidate": "本轮没有满足条件的触发",
    "silence": "处于静默时段",
    "invalid_time_config": "时间配置非法，按 fail-safe 处理",
    "min_interval": "未到主动发言最小间隔",
    "cooldown": "处于用户冷却期",
    "budget": "当日发言上限已用完",
    "dice": "概率未通过",
    "already_sent_today": "今天已经发过（每天一次）",
    "no_user_message": "还没有用户消息记录",
    "miss_duration": "想念时长条件未满足",
    "future_schedule": "未来 2 小时内有日程节点",
    "llm_rejected": "想念把关被 LLM 驳回",
    "planner_trigger_failed": "触发未入队（stream_id 缺失或开关关闭）",
    "disabled": "主动触发开关已关闭",
    "daily_max_zero": "每日发言上限为 0",
    # v2.4.3：外部日程与主动发言回执
    "external_schedule_fresh": "外部日程拉到节点",
    "external_schedule_cached": "外部日程命中缓存",
    "external_schedule_empty": "外部日程返回空（对方今日无日程）",
    "external_schedule_error": "外部日程拉取失败",
    "external_schedule_exception": "外部日程读取异常",
    "reply_confirmed": "planner 已生成并发送回复",
    "tool_send_message": "planner 通过 Tool 主动发消息",
}


class MaiLoverPlugin(MaiBotPlugin):
    """麦麦恋人插件主类。

    组装所有模块，管理插件生命周期：
    - on_load: 初始化所有子模块，缓存人设，启动调度器
    - on_unload: 停止调度器，刷新好感度数据
    - on_config_update: 热重载配置，scope="bot" 时刷新人设
    - on_planner_before_request: 注入麦麦当前活动到 planner extra_prompt
    - Tools: mai_lover_status / mai_lover_schedule / mai_lover_send_message /
             mai_lover_affection / mai_lover_config / mai_lover_current_activity
    - Commands: /mai_status / /mai_schedule / /mai_affection / /mai_help /
                /mai_config / /mai_llm_log / /mai_test
    """

    config_model = MaiLoverPluginSettings

    # 订阅主程序 bot 配置热重载（含 personality.personality）
    config_reload_subscriptions: ClassVar[Iterable[str]] = ("bot",)

    def __init__(self) -> None:
        super().__init__()
        self._affection_mgr: Optional[AffectionManager] = None
        self._memory_mgr: Optional[MemoryManager] = None
        self._llm_svc: Optional[LLMService] = None
        self._llm_logger: Optional[LLMCallLogger] = None
        # 主动行为决策日志（v2.4.2）：记录每轮巡检的判定结论，供 /mai_diag 查看
        self._decision_logger: Optional[ProactiveDecisionLogger] = None
        self._cateye: Optional[CateyeClient] = None
        self._message_svc: Optional[MessageService] = None
        self._holiday_svc: Optional[HolidayService] = None
        self._external_src: Optional[ExternalScheduleSource] = None
        self._schedule_gen: Optional[ScheduleGenerator] = None
        self._scheduler: Optional[Scheduler] = None
        self._cached_stream_id: str = ""
        self._cached_personality: str = ""
        self._stream_retry_task: Optional[asyncio.Task[Any]] = None
        self._cached_nickname: str = ""

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def on_load(self) -> None:
        """初始化插件：创建所有子模块，缓存人设，启动调度器。"""
        data_dir = self._get_data_dir()
        self.ctx.logger.info(f"MaiLover 数据目录: {data_dir}")
        os.makedirs(data_dir, exist_ok=True)

        # 数据迁移：旧式 <plugin>/data/ → ctx.paths.data_dir（SDK 2.6.0+）
        self._migrate_old_data_dir(data_dir)

        if not self.config.plugin.enabled:
            self.ctx.logger.info("MaiLover 插件已禁用（plugin.enabled=false），跳过初始化")
            return

        # 先读主程序配置（人格 + bot.昵称），后续子模块需要用到
        await self._refresh_bot_config()

        self._affection_mgr = AffectionManager(data_dir)
        self._affection_mgr.update_level(self.config.affection.current_level)
        self._memory_mgr = MemoryManager(self._affection_mgr)
        # LLM 调用日志（v2.4.0）：记录插件发起的全部模型请求回复，
        # 供 /mai_llm_log 合并转发查看；加载时顺手清理过期文件
        self._llm_logger = LLMCallLogger(
            data_dir,
            enabled=self.config.llm_log.enabled,
            retention_days=self.config.llm_log.retention_days,
        )
        self._llm_logger.cleanup()
        # 主动行为决策日志（v2.4.2）：主动私聊本身不调用插件的 LLM，所以
        # "为什么又发了 / 今天怎么没发"只能靠这份日志回答（见 decision_logger 模块说明）
        self._decision_logger = ProactiveDecisionLogger(
            data_dir,
            enabled=self.config.proactive_log.enabled,
            retention_days=self.config.proactive_log.retention_days,
            record_skips=self.config.proactive_log.record_skips,
        )
        self._decision_logger.cleanup()
        self._llm_svc = LLMService(self.ctx, self.config, call_logger=self._llm_logger)
        lover_name = self._get_lover_name()
        self._message_svc = MessageService(
            self.ctx, self.config, self._affection_mgr, lover_name
        )
        self._holiday_svc = HolidayService(self.config)
        # 外部日程源：use_external_schedule 开启时，ScheduleGenerator 从中读取
        # 自主规划插件（xuqian13.autonomous-planning-plugin-v4）的日程
        self._external_src = ExternalScheduleSource(self.ctx)
        self._schedule_gen = ScheduleGenerator(
            data_dir, self.config, self._llm_svc, self._holiday_svc,
            external_source=self._external_src,
        )
        # 恋人电脑联动（v2.4.0）：想念/早晚安触发时查看恋人在电脑上干什么
        self._cateye = CateyeClient(self.ctx, self.config, self._llm_svc)

        # 创建调度器（v2.0.0: 仅 4 个依赖，不再传 message_svc/memory_mgr；
        # v2.3.0: 传入 LLM 服务供想念触发前的 LLM 把关使用；
        # v2.4.0: 传入恋人电脑客户端供触发时查看电脑状态；
        # v2.4.2: 传入主动行为决策日志）
        self._scheduler = Scheduler(
            self.ctx, self.config, self._affection_mgr, self._schedule_gen,
            llm_service=self._llm_svc,
            cateye_client=self._cateye,
            decision_logger=self._decision_logger,
        )
        self._scheduler.set_personality(self._cached_personality)
        if lover_name:
            self._scheduler.set_lover_name(lover_name)

        target_qq = str(self.config.whitelist.target_qq)
        if not target_qq or target_qq == "123456789":
            self.ctx.logger.warning("target_qq 未配置或为默认值，请修改")

        await self._start_scheduler()

        # 预热当天节假日缓存，避免首次 planner 请求阻塞在 API 调用
        if self._holiday_svc:
            today = datetime.now().strftime("%Y-%m-%d")
            asyncio.create_task(self._holiday_svc.get_holiday_info(today))

        self.ctx.logger.info("MaiLover 插件加载完成")

    async def on_unload(self) -> None:
        """卸载插件：停止调度器，刷新好感度持久化。"""
        self.ctx.logger.info("MaiLover 插件正在卸载...")
        if self._affection_mgr is not None:
            self._affection_mgr.flush()
        if self._scheduler is not None:
            self._scheduler.stop()
        if self._stream_retry_task is not None:
            self._stream_retry_task.cancel()
            try:
                await asyncio.wait_for(self._stream_retry_task, timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
            self._stream_retry_task = None
        self.ctx.logger.info("MaiLover 插件已卸载")

    async def on_config_update(
        self, scope: str, config_data: dict[str, Any], version: str
    ) -> None:
        """热重载。

        self.config 已由 MaiBot 自动更新为最新值。
        scope="bot" 时额外刷新人设缓存（主程序人格配置变更）。
        """
        self.ctx.logger.info(f"配置热更新: scope={scope}, version={version}")

        if scope == "bot":
            await self._refresh_bot_config()
            if self._scheduler is not None:
                self._scheduler.set_personality(self._cached_personality)
                self._scheduler.set_lover_name(self._get_lover_name())
            self.ctx.logger.info("主程序人设已同步，无需重启 MaiLover 调度器")
            return None

        if not self.config.plugin.enabled:
            if self._scheduler is not None:
                self._scheduler.stop()
            return None

        if self._affection_mgr is not None:
            self._affection_mgr.update_level(self.config.affection.current_level)
            self._affection_mgr.flush()

        # 同步子模块配置引用
        if self._llm_svc is not None:
            self._llm_svc._config = self.config
        if self._message_svc is not None:
            self._message_svc._config = self.config
        if self._schedule_gen is not None:
            self._schedule_gen._config = self.config
        if self._cateye is not None:
            self._cateye._config = self.config
        if self._llm_logger is not None:
            self._llm_logger.update_settings(
                self.config.llm_log.enabled,
                self.config.llm_log.retention_days,
            )
        if self._decision_logger is not None:
            self._decision_logger.update_settings(
                self.config.proactive_log.enabled,
                self.config.proactive_log.retention_days,
                self.config.proactive_log.record_skips,
            )

        # 停止旧调度器并重建实例（v2.3.1：复用同一实例时，旧循环若正卡在
        # tick 中，start() 清除 stop_event 后会与新循环并存，形成双循环竞态）
        if self._scheduler is not None:
            self._scheduler.stop()
        if self._affection_mgr is not None and self._schedule_gen is not None:
            self._scheduler = Scheduler(
                self.ctx, self.config, self._affection_mgr, self._schedule_gen,
                llm_service=self._llm_svc,
                cateye_client=self._cateye,
                decision_logger=self._decision_logger,
            )
            self._scheduler.set_personality(self._cached_personality)
            lover_name = self._get_lover_name()
            if lover_name:
                self._scheduler.set_lover_name(lover_name)
        await self._start_scheduler()

        self.ctx.logger.info("配置热更新完成")
        return None

    # ── HookHandler ────────────────────────────────────────────────────

    @HookHandler(
        "chat.receive.after_process",
        name="mai_lover_target_private_message_observer",
        description="记录白名单用户的私聊时间，供主动聊天冷却和想念机制使用",
        mode=HookMode.OBSERVE,
        order=HookOrder.LATE,
        timeout_ms=1000,
        error_policy=ErrorPolicy.SKIP,
    )
    async def on_target_private_message(
        self, message: dict[str, Any] | None = None, **kwargs: Any
    ) -> None:
        """记录目标用户的入站私聊；群聊和其他用户不影响恋人调度。"""
        del kwargs
        if not self._affection_mgr or not self._is_target_private_message(message):
            return

        self._affection_mgr.update_last_user_msg_time(datetime.now())
        self.ctx.logger.debug("已更新目标用户最后私聊时间")

    def _is_target_private_message(self, message: Any) -> bool:
        if not isinstance(message, dict) or message.get("is_notify"):
            return False
        message_info = message.get("message_info")
        if not isinstance(message_info, dict) or message_info.get("group_info"):
            return False
        user_info = message_info.get("user_info")
        if not isinstance(user_info, dict):
            return False
        user_id = str(user_info.get("user_id", "")).strip()
        target_qq = str(self.config.whitelist.target_qq).strip()
        return bool(user_id and target_qq and user_id == target_qq)

    @HookHandler("maisaka.planner.before_request")
    async def on_planner_before_request(self, **kwargs: Any) -> dict[str, Any]:
        """注入麦麦当前活动状态到 planner 上下文。

        每次 planner 请求时触发（含用户正常回复和 proactive trigger），
        让麦麦在任何时候都知道自己在做什么。

        注入方式（v2.3.1 修复）：宿主在 planner 请求后只回读 ``items``
        （1.2.x ContextItem 快照投影）或 ``messages``（更早版本 role/content
        投影）——往 ``extra_prompt`` 写值会被宿主忽略（那是
        ``maisaka.replyer.before_request`` 的字段），因此按入参投影构造一条
        system 消息插入上下文，其余 kwargs 全量回传。
        """
        if not self._schedule_gen or not self._holiday_svc:
            return {"action": "continue", "modified_kwargs": kwargs}

        # 兼容新旧 payload 字段：优先 items，回退 messages
        items = kwargs.get("items")
        messages = kwargs.get("messages")
        if isinstance(items, list) and items:
            payload_key, payload = "items", items
        elif isinstance(messages, list) and messages:
            payload_key, payload = "messages", messages
        else:
            return {"action": "continue", "modified_kwargs": kwargs}

        # [LOCAL-PATCH:cateye] 超限不再整体跳过：先把最大的图片 part 换成文本占位符再注入。
        payload, omitted_images = self._shrink_payload_for_injection(payload)
        if payload is None:
            self.ctx.logger.warning(
                f"planner payload 过大且裁剪后仍超限 (>{_MAX_INJECT_PAYLOAD_BYTES} bytes)，跳过活动注入"
            )
            return {"action": "continue", "modified_kwargs": kwargs}
        if omitted_images:
            self.ctx.logger.info(
                f"planner payload 过大，已省略 {omitted_images} 张图片以完成活动注入"
            )

        suffix = await self._build_activity_suffix()
        # [LOCAL-PATCH:cateye] 主动发言回合追加回复目标约束（宿主无法强制目标，只能软约束）
        suffix += self._build_proactive_target_rule()
        if not suffix:
            return {"action": "continue", "modified_kwargs": kwargs}

        modified_payload = self._inject_context_message(payload, suffix)
        modified_kwargs: dict[str, Any] = {**kwargs, payload_key: modified_payload}
        return {"action": "continue", "modified_kwargs": modified_kwargs}

    async def _build_activity_suffix(self) -> str:
        """构造当前活动注入文本（日期/节假日/当前活动/好感度）。"""
        now = datetime.now()
        current_date = now.strftime("%Y-%m-%d")
        weekday = "一二三四五六日"[now.weekday()]
        holiday_info = await self._holiday_svc.get_holiday_info(current_date)
        current_time = now.strftime("%H:%M")
        name = self._get_lover_name()
        affection_level = self._affection_mgr.level() if self._affection_mgr else 0
        affection_desc = AFFECTION_DESCRIPTIONS.get(affection_level, "")

        suffix = f"\n【当前日期】今天是 {current_date}（星期{weekday}），{holiday_info}。"

        # v2.3.0：无日程时不注入日程状态。外部日程模式下，自主规划插件
        # 无睡眠时段不生成日程、凌晨日切后当日日程也可能尚未生成——
        # 此时不再出现"麦麦正在今天还没有安排"这类占位句。
        activity = self._schedule_gen.find_current_activity(now)
        if activity:
            suffix += f"\n【{name}当前状态】现在 {current_time}，{name}正在{activity}。"
        else:
            self.ctx.logger.debug("当前无日程（外部日程可能未生成/时段无安排），跳过日程状态注入")

        suffix += f"\n【{name}对用户的好感度】档位 {affection_level}（{affection_desc}）"
        return suffix

    @staticmethod
    def _inject_context_message(
        payload: list[dict[str, Any]], text: str
    ) -> list[dict[str, Any]]:
        """把注入文本作为一条 system 消息插入上下文列表。

        兼容两种投影：
        - ContextItem 快照（MaiBot 1.2.x+，``item_type``/``meta``/``parts``）：
          构造合法的 SystemMessageItem 快照（item_id 全局唯一），否则宿主
          反序列化失败会丢弃整个 items 修改；
        - 旧 role/content 字典：构造 ``{"role": "system", "content": ...}``。

        插入位置：第一条 system 消息之后；没有则插到列表开头。
        返回浅拷贝副本，不污染调用方列表。
        """
        is_snapshot = any(
            isinstance(m, dict) and isinstance(m.get("item_type"), str) and m.get("item_type")
            for m in payload
        )
        if is_snapshot:
            logical_turn_id: Optional[str] = None
            for m in payload:
                meta = m.get("meta") if isinstance(m, dict) else None
                if (
                    isinstance(meta, dict)
                    and isinstance(meta.get("logical_turn_id"), str)
                    and meta["logical_turn_id"].strip()
                ):
                    logical_turn_id = meta["logical_turn_id"]
                    break
            injection: dict[str, Any] = {
                "item_type": "SystemMessageItem",
                "meta": {
                    "item_id": f"mailover-context-{uuid4().hex}",
                    "logical_turn_id": logical_turn_id,
                    "timestamp": datetime.now().astimezone().isoformat(),
                },
                "parts": [{"type": "text", "text": text}],
            }
        else:
            injection = {"role": "system", "content": text}

        def _role(m: Any) -> str:
            if not isinstance(m, dict):
                return ""
            item_type = m.get("item_type")
            if isinstance(item_type, str) and item_type:
                if item_type == "SystemMessageItem":
                    return "system"
                return item_type
            role = m.get("role")
            return role if isinstance(role, str) else ""

        first_system_idx: Optional[int] = None
        for idx, m in enumerate(payload):
            if _role(m) == "system":
                first_system_idx = idx
                break

        new_payload = list(payload)
        insert_at = (first_system_idx + 1) if first_system_idx is not None else 0
        new_payload.insert(insert_at, injection)
        return new_payload

    # ── [LOCAL-PATCH:cateye] 以下两个辅助方法为本地新增 ────────────────────

    @staticmethod
    def _payload_json_size(payload: Any) -> int:
        """估算 payload 的 JSON 体积（与宿主 RPC 帧编码量级一致）。"""

        import json as _json

        return len(_json.dumps(payload, default=str, ensure_ascii=False))

    @classmethod
    def _shrink_payload_for_injection(
        cls, payload: list[dict[str, Any]]
    ) -> tuple[Optional[list[dict[str, Any]]], int]:
        """payload 超过阈值时，按体积从大到小把图片 part 换成文本占位符。

        宿主把图片以 ``image_base64`` 内联进 items 快照（``request_snapshot.py:264-271``），
        所以带图的上下文常态就是 2–12MB，而宿主单帧上限是 16MB（``transport/base.py:18``）。
        原先"超过 1MB 就整体跳过注入"会让活动注入几乎永不生效；这里改为省略最大的图片
        part——替换成文本占位符而不是删掉，保证 item 仍有 part、宿主反序列化不会失败。

        Returns:
            ``(可注入的 payload 或 None, 被省略的图片数)``；``None`` 表示裁剪后仍超限。
        """

        size = cls._payload_json_size(payload)
        if size <= _MAX_INJECT_PAYLOAD_BYTES:
            return payload, 0

        candidates: list[tuple[int, int, int]] = []
        for item_index, item in enumerate(payload):
            if not isinstance(item, dict):
                continue
            parts = item.get("parts")
            if not isinstance(parts, list):
                continue
            for part_index, part in enumerate(parts):
                if isinstance(part, dict) and part.get("type") == "image":
                    candidates.append((cls._payload_json_size(part), item_index, part_index))
        candidates.sort(reverse=True)

        placeholder: dict[str, Any] = {"type": "text", "text": _IMAGE_OMITTED_PLACEHOLDER}
        placeholder_size = cls._payload_json_size(placeholder)
        items: list[Any] = [dict(item) if isinstance(item, dict) else item for item in payload]
        omitted = 0
        for part_size, item_index, part_index in candidates:
            if size <= _MAX_INJECT_PAYLOAD_BYTES:
                break
            item = items[item_index]
            parts = list(item.get("parts") or [])
            if part_index >= len(parts):
                continue
            parts[part_index] = dict(placeholder)
            item["parts"] = parts
            size = size - part_size + placeholder_size
            omitted += 1

        if omitted == 0 or size > _MAX_INJECT_PAYLOAD_BYTES:
            return None, omitted
        return items, omitted

    def _build_proactive_target_rule(self) -> str:
        """主动发言回合给 planner 的回复目标约束（软约束，宿主没有强制目标的参数）。"""

        if self._scheduler is None:
            return ""
        last_trigger = self._scheduler.get_last_trigger_time()
        if last_trigger is None:
            return ""
        if (datetime.now() - last_trigger).total_seconds() > _PROACTIVE_RULE_WINDOW_SECONDS:
            return ""
        return (
            "\n【本轮是主动发言】现在是你在主动找对方说话（不是对方来找你）："
            "\n- 不要回复你自己发送的消息，也不要在发言里引用你自己的消息；"
            "\n- 优先回应对方最后一条发言；如果最后一条发言是你自己的（对方还没回你），"
            "就当作对方还没回，直接说新内容或起一个新话题；"
            "\n- reply 的 msg_id 只能选对方发送的消息。"
        )

    @HookHandler("maisaka.replyer.after_response", mode=HookMode.OBSERVE)
    async def on_replyer_after_response(self, **kwargs: Any) -> None:
        """planner 实际生成回复时，补计 0.5 触发数，并在决策日志里记一条"确认发言"。

        与 _trigger_planner 的 0.5 配合：回复成功总计 1.0，未回复总计 0.5。
        仅在最近 90 秒内有 proactive trigger 时生效，避免误匹配用户消息的回复。
        """
        if not self._scheduler or not self._affection_mgr:
            return

        last_trigger = self._scheduler.get_last_trigger_time()
        if last_trigger is None:
            return  # 没有待确认的 proactive trigger

        now = datetime.now()
        elapsed = (now - last_trigger).total_seconds()
        # 90 秒窗口内的回复视为对 proactive trigger 的响应
        if elapsed < 90:
            trigger_type = self._scheduler.get_last_trigger_intent()
            self._affection_mgr.increment_speak(0.5)  # 补计 0.5
            self._scheduler.clear_last_trigger_time()
            self.ctx.logger.debug("replyer 回复 detected，补计 0.5 触发数")
            # v2.4.3：主动触发只代表"已入队"，这一条才是"planner 真的生成并发出去了"的回执
            self._log_proactive_event(
                action=ACTION_SPOKEN,
                reason="reply_confirmed",
                trigger_type=trigger_type,
                detail=(
                    f"planner 已生成回复并发送（距触发 {elapsed:.0f} 秒）"
                    f"｜触发类型={trigger_type or '未知'}"
                ),
            )

    def _log_proactive_event(
        self, *, action: str, reason: str, trigger_type: str = "", detail: str = "", **fields: Any
    ) -> None:
        """把主动发言相关事件写进决策日志（未启用日志时静默 no-op）。

        v2.4.3 新增：让"所有主动发言行为"都有记录，包括
        planner 确认发言（``spoken``）、Tool 主动发消息（``trigger``/``tool``）、
        以及外部日程拉取结果（``info``/``schedule_source``）。
        """

        if self._decision_logger is None:
            return
        try:
            self._decision_logger.record(
                action=action, reason=reason, trigger_type=trigger_type, detail=detail, **fields
            )
        except Exception as e:  # noqa: BLE001 - 日志故障绝不影响正常流程
            self.ctx.logger.debug(f"写主动行为决策日志失败（忽略）: {e}")

    # ── Tools (LLM 可主动调用) ─────────────────────────────────────────
    # 注意：stream_id 由插件内部维护，LLM 调用时无需传入。

    @Tool(
        name="mai_lover_current_activity",
        description="查询麦麦现在正在做什么。当用户问'在干嘛''在做什么'或想了解麦麦当前状态时调用。",
    )
    async def tool_mai_lover_current_activity(self, **kwargs: Any) -> str:
        """LLM Tool: 查询麦麦当前活动。"""
        if not self._schedule_gen:
            return "日程服务未初始化。"
        activity = self._schedule_gen.get_current_activity(datetime.now())
        name = self._get_lover_name()
        return f"{name}现在正在{activity}。"

    @Tool(
        name="mai_lover_status",
        description="查看麦麦恋人的当前状态：好感度档位、今日发言次数、日程摘要",
    )
    async def tool_mai_lover_status(self, **kwargs: Any) -> str:
        """LLM Tool: 查看麦麦状态。"""
        return self._build_status_report()

    @Tool(
        name="mai_lover_schedule",
        description="查看麦麦的今日完整日程安排",
    )
    async def tool_mai_lover_schedule(self, **kwargs: Any) -> str:
        """LLM Tool: 查看今日日程。"""
        return self._build_schedule_report()

    @Tool(
        name="mai_lover_send_message",
        description="以麦麦恋人的口吻向用户主动发送一条恋人消息",
        parameters={
            "message": {
                "type": "string",
                "description": "要发送的消息文本（可选，留空则自动生成）",
                "required": False,
            },
        },
    )
    async def tool_mai_lover_send_message(
        self, message: str = "", **kwargs: Any
    ) -> str:
        """LLM Tool: 主动发送恋人消息。"""
        stream_id = self._cached_stream_id
        name = self._get_lover_name()
        if not stream_id:
            return f"{name}还没有连接到目标用户，请稍后再试。"
        if not self._message_svc or not self._llm_svc:
            return "消息或 LLM 服务未初始化，无法发送。"

        if not message:
            message = await self._llm_svc.generate_or_fallback(
                prompt="请用温柔恋人的口吻说一句问候或分享一件小事（20-40字）。",
                fallback="想你了呢~在忙什么呀？",
                system_prompt=f"你是一个温柔体贴的虚拟恋人「{name}」。",
                temperature=0.8,
                event="tool_send_message",
            )

        final_text = self._message_svc.append_affection_suffix(message)
        try:
            result = await self.ctx.send.text(text=final_text, stream_id=stream_id)
            if result:
                if self._affection_mgr:
                    self._affection_mgr.increment_speak()
                # v2.4.3：Tool 主动发消息不走巡检，也必须进决策日志，否则"所有主动发言行为"就有漏网
                self._log_proactive_event(
                    action=ACTION_TRIGGER,
                    reason="tool_send_message",
                    trigger_type="tool",
                    detail=f"planner 通过 Tool 主动发了一条消息：{final_text[:40]}",
                )
                return f"消息已发送: {final_text[:60]}..."
            return f"消息发送失败: 发送返回 False"
        except Exception as e:
            self.ctx.logger.error(f"[tool_mai_lover_send_message] 发送异常: {e}")
            return f"消息发送异常: {e}"

    @Tool(
        name="mai_lover_affection",
        description="调整麦麦恋人的好感度档位。0=熟悉（温柔拘谨），1=亲密（活泼热情），2=热恋（撒娇抱抱）",
        parameters={
            "level": {
                "type": "integer",
                "description": "好感度档位，只能填 0、1 或 2",
                "required": True,
            },
        },
    )
    async def tool_mai_lover_affection(self, level: int = 0, **kwargs: Any) -> str:
        """LLM Tool: 调整好感度。"""
        if not self._affection_mgr:
            return "好感度管理器未初始化。"
        if level not in (0, 1, 2):
            return f"档位只能填 0（熟悉）、1（亲密）或 2（热恋），收到的是 {level}。"
        self._affection_mgr.update_level(level)
        descs = {0: "熟悉（温柔拘谨）", 1: "亲密（活泼热情）", 2: "热恋（撒娇抱抱）"}
        self.ctx.logger.info(f"好感度已通过 Tool 调整为 {level}")
        return f"好感度已更新: {level} - {descs[level]}"

    @Tool(
        name="mai_lover_config",
        description="查看麦麦恋人当前插件配置：巡检间隔、时间窗口、概率、今日发言上限、好感度",
    )
    async def tool_mai_lover_config(self, **kwargs: Any) -> str:
        """LLM Tool: 查看配置。"""
        s = self.config.schedule
        t = self.config.time_windows
        p = self.config.probability
        a = self.config.affection
        schedule_source = "外部（自主规划插件）" if s.use_external_schedule else "本插件自动生成"
        miss_llm = "开" if getattr(t, "miss_llm_check_enabled", False) else "关"
        return (
            "⚙️ 麦麦恋人配置: "
            f"巡检间隔 {s.check_interval_minutes}min | "
            f"每日上限 {s.daily_max_speak} 条 | "
            f"冷却 {s.user_cooldown_minutes}min | "
            f"触发开关 {'开' if s.proactive_trigger_enabled else '关'} | "
            f"日程来源 {schedule_source} | "
            f"早安 {t.morning_start}~{t.morning_end} | "
            f"晚安 {t.night_start}~{t.night_end} | "
            f"想念触发 {t.miss_trigger_hours_min}~{t.miss_trigger_hours_max}h"
            f"（LLM把关{miss_llm}） | "
            f"日常概率 {p.default_speak_rate} | "
            f"想念概率 {p.miss_speak_rate} | "
            f"日程节点概率 {p.activity_trigger_rate} | "
            f"好感度 {a.current_level} | "
            f"模型 {self.config.plugin.llm_model}"
        )

    # ── API (供其他插件调用) ────────────────────────────────────────────

    @API(
        name="get_current_activity",
        description="获取麦麦当前正在做什么。返回活动描述字符串。",
        version="1",
        public=True,
    )
    async def api_get_current_activity(self, **kwargs: Any) -> str:
        """API: 获取麦麦当前活动。"""
        if not self._schedule_gen:
            return ""
        return self._schedule_gen.get_current_activity(datetime.now())

    @API(
        name="get_schedule",
        description="获取麦麦今日完整日程。返回节点列表 [{time, activity}]。",
        version="1",
        public=True,
    )
    async def api_get_schedule(self, **kwargs: Any) -> list[dict[str, Any]]:
        """API: 获取今日日程。"""
        if not self._schedule_gen:
            return []
        today_str = datetime.now().strftime("%Y-%m-%d")
        return self._schedule_gen.load_cached_schedule(today_str)

    @API(
        name="get_affection_level",
        description="获取当前好感度档位。返回 0/1/2。",
        version="1",
        public=True,
    )
    async def api_get_affection_level(self, **kwargs: Any) -> int:
        """API: 获取好感度档位。"""
        if not self._affection_mgr:
            return 0
        return self._affection_mgr.level()

    # ── Commands (用户手动交互) ────────────────────────────────────────

    @Command(name="/mai_status", pattern=r"(?<!\S)/mai_status\s*$", description="查看麦麦恋人状态（好感度、发言计数、日程摘要）")
    async def cmd_mai_status(self, **kwargs: Any) -> tuple[bool, str, int]:
        """查看麦麦恋人状态。主动发送报告给用户并拦截消息。"""
        if not self._authorize_command(kwargs):
            return True, "没有权限", 2
        stream_id = str(kwargs.get("stream_id", ""))
        report = self._build_status_report()
        try:
            await self.ctx.send.text(text=report, stream_id=stream_id)
            return True, "状态已发送", 2
        except Exception as e:
            self.ctx.logger.error(f"cmd_mai_status 发送失败: {e}")
            return False, f"发送失败: {e}", 2

    @Command(name="/mai_schedule", pattern=r"(?<!\S)/mai_schedule\s*$", description="查看麦麦今日完整日程")
    async def cmd_mai_schedule(self, **kwargs: Any) -> tuple[bool, str, int]:
        """查看麦麦今日完整日程。主动发送报告给用户并拦截消息。"""
        if not self._authorize_command(kwargs):
            return True, "没有权限", 2
        stream_id = str(kwargs.get("stream_id", ""))
        report = self._build_schedule_report()
        try:
            await self.ctx.send.text(text=report, stream_id=stream_id)
            return True, "日程已发送", 2
        except Exception as e:
            self.ctx.logger.error(f"cmd_mai_schedule 发送失败: {e}")
            return False, f"发送失败: {e}", 2

    @Command(
        name="/mai_affection",
        pattern=r"(?<!\S)/mai_affection(?:\s+(?P<mai_level>\S+))?\s*$",
        description="调整好感度档位。用法: /mai_affection <0|1|2>",
    )
    async def cmd_mai_affection(self, **kwargs: Any) -> tuple[bool, str, int]:
        """调整好感度档位。解析参数、发送确认/用法消息给用户并拦截消息。"""
        if not self._authorize_command(kwargs):
            return True, "没有权限", 2
        stream_id = str(kwargs.get("stream_id", ""))
        # 参数优先取正则命名捕获（兼容"引用+命令"场景下 text 前缀混入其他内容）
        matched_groups = kwargs.get("matched_groups")
        level_str = ""
        if isinstance(matched_groups, dict):
            level_str = str(matched_groups.get("mai_level", "") or "").strip()
        if not level_str:
            raw_message = str(kwargs.get("text", "")).strip()
            parts = raw_message.split()
            if len(parts) >= 2 and parts[0].endswith("/mai_affection"):
                level_str = parts[1]
        if not level_str:
            current = self._affection_mgr.level() if self._affection_mgr else "?"
            usage = (
                f"用法: /mai_affection <0|1|2>\n"
                f"0 = 熟悉（温柔拘谨）\n"
                f"1 = 亲密（活泼热情）\n"
                f"2 = 热恋（撒娇抱抱）\n"
                f"当前档位: {current}"
            )
            try:
                await self.ctx.send.text(text=usage, stream_id=stream_id)
            except Exception as e:
                self.ctx.logger.error(f"cmd_mai_affection 用法发送失败: {e}")
            return False, "参数错误", 2
        try:
            level = int(level_str)
        except (ValueError, TypeError):
            msg = f"「{level_str}」不是有效数字，请使用 0、1 或 2。"
            try:
                await self.ctx.send.text(text=msg, stream_id=stream_id)
            except Exception as e:
                self.ctx.logger.error(f"cmd_mai_affection 错误提示发送失败: {e}")
            return False, "参数错误", 2
        if level not in (0, 1, 2):
            msg = f"好感度档位只能是 0（熟悉）、1（亲密）或 2（热恋），收到的是 {level}。"
            try:
                await self.ctx.send.text(text=msg, stream_id=stream_id)
            except Exception as e:
                self.ctx.logger.error(f"cmd_mai_affection 错误提示发送失败: {e}")
            return False, "参数错误", 2
        if not self._affection_mgr:
            msg = "好感度管理器未初始化，无法调整。"
            try:
                await self.ctx.send.text(text=msg, stream_id=stream_id)
            except Exception as e:
                self.ctx.logger.error(f"cmd_mai_affection 错误提示发送失败: {e}")
            return False, "管理器未初始化", 2
        self._affection_mgr.update_level(level)
        descs = {0: "熟悉（温柔拘谨）", 1: "亲密（活泼热情）", 2: "热恋（撒娇抱抱）"}
        self.ctx.logger.info(f"好感度已通过命令调整为 {level}")
        confirm = f"好感度已更新: {level} - {descs.get(level, '未知')}"
        try:
            await self.ctx.send.text(text=confirm, stream_id=stream_id)
            return True, "好感度已调整", 2
        except Exception as e:
            self.ctx.logger.error(f"cmd_mai_affection 确认发送失败: {e}")
            return False, f"发送失败: {e}", 2

    @Command(name="/mai_help", pattern=r"(?<!\S)/mai_help\s*$", description="查看麦麦恋人所有可用命令")
    async def cmd_mai_help(self, **kwargs: Any) -> tuple[bool, str, int]:
        """查看所有可用命令。主动发送帮助文本给用户并拦截消息。"""
        if not self._authorize_command(kwargs):
            return True, "没有权限", 2
        stream_id = str(kwargs.get("stream_id", ""))
        name = self._get_lover_name()
        help_text = (
            "🐱 麦麦恋人 可用命令:\n"
            f"/mai_status    — 查看{name}状态（好感度/今日发言/日程摘要）\n"
            "/mai_schedule  — 查看今日完整日程\n"
            "/mai_affection — 调整好感度档位: /mai_affection <0|1|2>\n"
            "/mai_config    — 查看当前插件配置摘要\n"
            "/mai_llm_log   — 查看 LLM 调用日志: /mai_llm_log [天数]\n"
            "/mai_diag      — 查看主动行为决策日志（为什么发言/为什么没发言）: /mai_diag [天数]\n"
            "/mai_test      — 发送一条测试消息（验证发送通道）"
        )
        try:
            await self.ctx.send.text(text=help_text, stream_id=stream_id)
            return True, "帮助已发送", 2
        except Exception as e:
            self.ctx.logger.error(f"cmd_mai_help 发送失败: {e}")
            return False, f"发送失败: {e}", 2

    @Command(name="/mai_config", pattern=r"(?<!\S)/mai_config\s*$", description="查看麦麦恋人当前配置摘要")
    async def cmd_mai_config(self, **kwargs: Any) -> tuple[bool, str, int]:
        """查看当前配置摘要。主动发送配置信息给用户并拦截消息。"""
        if not self._authorize_command(kwargs):
            return True, "没有权限", 2
        stream_id = str(kwargs.get("stream_id", ""))
        s = self.config.schedule
        t = self.config.time_windows
        p = self.config.probability
        a = self.config.affection
        schedule_source = "外部（自主规划插件）" if s.use_external_schedule else "本插件自动生成"
        miss_llm = "开" if getattr(t, "miss_llm_check_enabled", False) else "关"
        summary = (
            "⚙️ 麦麦恋人配置摘要\n"
            f"调度: 巡检间隔 {s.check_interval_minutes}min | "
            f"每日上限 {s.daily_max_speak} 条 | "
            f"冷却 {s.user_cooldown_minutes}min | "
            f"触发开关 {'开启' if s.proactive_trigger_enabled else '关闭'}\n"
            f"日程来源: {schedule_source} | "
            f"生成时间: 凌晨 {s.generate_hour}:00（外部模式下不生成）\n"
            f"时间窗口: 早安 {t.morning_start}~{t.morning_end} | "
            f"晚安 {t.night_start}~{t.night_end} | "
            f"想念触发 {t.miss_trigger_hours_min}~{t.miss_trigger_hours_max}h"
            f"（LLM把关{miss_llm}）\n"
            f"概率: 日常巡检 {p.default_speak_rate} | "
            f"想念 {p.miss_speak_rate} | "
            f"日程节点 {p.activity_trigger_rate}\n"
            f"好感度: {a.current_level} | "
            f"模型: {self.config.plugin.llm_model}"
        )
        try:
            await self.ctx.send.text(text=summary, stream_id=stream_id)
            return True, "配置已发送", 2
        except Exception as e:
            self.ctx.logger.error(f"cmd_mai_config 发送失败: {e}")
            return False, f"发送失败: {e}", 2

    @Command(
        name="/mai_llm_log",
        pattern=r"(?<!\S)/mai_llm_log(?:\s+(?P<mai_days>\S+))?\s*$",
        description="查看插件发起的 LLM 调用日志（合并转发）。用法: /mai_llm_log [天数]",
    )
    async def cmd_mai_llm_log(
        self, mai_days: str = "", **kwargs: Any
    ) -> tuple[bool, str, int]:
        """查看 LLM 调用日志：每次请求一条消息，合并转发发出。

        可选天数参数限定回看范围（默认 = 配置的保留天数）。
        """
        if not self._authorize_command(kwargs):
            return True, "没有权限", 2
        stream_id = str(kwargs.get("stream_id", ""))

        if self._llm_logger is None or not self._llm_logger.enabled:
            msg = "LLM 调用日志未启用（配置 [llm_log] enabled = true 后开始记录）。"
            try:
                await self.ctx.send.text(text=msg, stream_id=stream_id)
            except Exception as e:
                self.ctx.logger.error(f"cmd_mai_llm_log 提示发送失败: {e}")
            return True, "日志未启用", 2

        days: Optional[int] = None
        if mai_days:
            try:
                days = max(1, min(30, int(str(mai_days).strip())))
            except ValueError:
                days = None

        entries = self._llm_logger.read_entries(days)
        if not entries:
            msg = f"最近 {days or self._llm_logger.retention_days} 天没有 LLM 调用日志。"
            try:
                await self.ctx.send.text(text=msg, stream_id=stream_id)
            except Exception as e:
                self.ctx.logger.error(f"cmd_mai_llm_log 空提示发送失败: {e}")
            return True, "暂无日志", 2

        records = self._build_llm_log_records(
            entries, self._llm_logger.retention_days
        )
        try:
            await self.ctx.send.forward(records, stream_id)
            return True, "日志已合并转发", 2
        except Exception as forward_exc:
            # 适配器不支持转发时回退为纯文本
            self.ctx.logger.warning(f"LLM 日志合并转发失败，回退纯文本: {forward_exc}")
            fallback = "\n\n".join(
                str(record["segments"][0]["content"]) for record in records
            )
            try:
                await self.ctx.send.text(text=fallback, stream_id=stream_id)
                return True, "日志已发送（文本回退）", 2
            except Exception as e:
                self.ctx.logger.error(f"cmd_mai_llm_log 回退发送失败: {e}")
                return False, f"发送失败: {e}", 2

    @staticmethod
    def _build_llm_log_records(
        entries: list[dict[str, Any]], retention_days: int
    ) -> list[dict[str, Any]]:
        """把日志条目构造成合并转发节点：每次请求一条消息。

        首条为汇总（时间范围 / 总条数 / 保留天数），其后按时间升序逐条
        展示「[时间] 事件 · 模型 · 状态 + 回复内容」。最多展示最近 50 条。
        """
        max_records = 50
        total = len(entries)
        shown = entries[-max_records:]

        first_time = str(entries[0].get("time", "?"))
        last_time = str(entries[-1].get("time", "?"))
        header = (
            "📋 麦麦恋人 LLM 调用日志\n"
            f"时间范围: {first_time} ~ {last_time}\n"
            f"共 {total} 条（展示最近 {len(shown)} 条） | 保留 {retention_days} 天"
        )
        records: list[dict[str, Any]] = [
            {
                "user_id": "0",
                "nickname": "LLM调用日志",
                "segments": [{"type": "text", "content": header}],
            }
        ]
        for entry in shown:
            status = "✅" if entry.get("success") else "❌"
            body = str(entry.get("response") or "")
            error = str(entry.get("error") or "")
            if error:
                body = f"{body}（错误: {error}）" if body else f"（错误: {error}）"
            line = (
                f"[{entry.get('time', '?')}] "
                f"{entry.get('event', '?')} · "
                f"{entry.get('model') or '默认模型'} · {status}\n"
                f"{body or '（空回复）'}"
            )
            records.append(
                {
                    "user_id": "0",
                    "nickname": "LLM调用日志",
                    "segments": [{"type": "text", "content": line}],
                }
            )
        return records

    @Command(
        name="/mai_diag",
        pattern=r"(?<!\S)/mai_diag(?:\s+(?P<mai_days>\S+))?\s*$",
        description="查看主动行为决策日志（每轮巡检一条：为什么发言/为什么没发言）。用法: /mai_diag [天数]",
    )
    async def cmd_mai_diag(
        self, mai_days: str = "", **kwargs: Any
    ) -> tuple[bool, str, int]:
        """查看主动行为决策日志：每轮巡检一条记录，合并转发发出。

        v2.4.2 新增。用于回答"明明配置了间隔，为什么还是发了""今天怎么一次都没发"——
        决策日志会逐轮给出判定依据（静默/最小间隔/冷却/概率/上限）与最终动作。
        """
        if not self._authorize_command(kwargs):
            return True, "没有权限", 2
        stream_id = str(kwargs.get("stream_id", ""))

        if self._decision_logger is None or not self._decision_logger.enabled:
            msg = "主动行为日志未启用（配置 [proactive_log] enabled = true 后开始记录）。"
            try:
                await self.ctx.send.text(text=msg, stream_id=stream_id)
            except Exception as e:
                self.ctx.logger.error(f"cmd_mai_diag 提示发送失败: {e}")
            return True, "日志未启用", 2

        days: Optional[int] = None
        if mai_days:
            try:
                days = max(1, min(30, int(str(mai_days).strip())))
            except ValueError:
                days = None

        entries = self._decision_logger.read_entries(days)
        if not entries:
            status_line = self._build_patrol_status_line()
            msg = (
                f"最近 {days or self._decision_logger.retention_days} 天没有主动行为决策记录。"
                "（插件刚加载或日志刚开启时属正常）"
            )
            if status_line:
                msg += f"\n{status_line}\n（若「巡检=未运行」，说明 stream_id 没解析出来，主动发言整条链路都没跑）"
            try:
                await self.ctx.send.text(text=msg, stream_id=stream_id)
            except Exception as e:
                self.ctx.logger.error(f"cmd_mai_diag 空提示发送失败: {e}")
            return True, "暂无日志", 2

        records = self._build_diag_records(
            entries, self._decision_logger.retention_days
        )
        try:
            await self.ctx.send.forward(records, stream_id)
            return True, "决策日志已合并转发", 2
        except Exception as forward_exc:
            # 适配器不支持转发时回退为纯文本
            self.ctx.logger.warning(f"决策日志合并转发失败，回退纯文本: {forward_exc}")
            fallback = "\n\n".join(
                str(record["segments"][0]["content"]) for record in records
            )
            try:
                await self.ctx.send.text(text=fallback, stream_id=stream_id)
                return True, "决策日志已发送（文本回退）", 2
            except Exception as e:
                self.ctx.logger.error(f"cmd_mai_diag 回退发送失败: {e}")
                return False, f"发送失败: {e}", 2

    def _build_diag_records(
        self, entries: list[dict[str, Any]], retention_days: int
    ) -> list[dict[str, Any]]:
        """把决策日志条目构造成合并转发节点。

        首条为汇总（时间范围、触发/确认发言/跳过计数、跳过原因分布、当前节奏配置、
        巡检与日程来源状态），其后按时间升序逐条展示；最多展示最近 30 条。
        """
        max_records = 30
        total = len(entries)
        shown = entries[-max_records:]
        triggered = sum(1 for e in entries if str(e.get("action")) == "trigger")
        spoken = sum(1 for e in entries if str(e.get("action")) == "spoken")
        info = sum(1 for e in entries if str(e.get("action")) == "info")
        skipped = sum(1 for e in entries if str(e.get("action")) == "skip")

        reason_counts: dict[str, int] = {}
        for entry in entries:
            if str(entry.get("action")) != "skip":
                continue
            key = str(entry.get("reason") or "unknown")
            reason_counts[key] = reason_counts.get(key, 0) + 1
        top_reasons = sorted(reason_counts.items(), key=lambda kv: kv[1], reverse=True)[:6]

        schedule_cfg = self.config.schedule
        windows = self.config.time_windows
        header = (
            "🔍 麦麦恋人 主动行为决策日志\n"
            f"时间范围: {entries[0].get('time', '?')} ~ {entries[-1].get('time', '?')}\n"
            f"共 {total} 条：发起触发 {triggered} / 确认发言 {spoken} / 跳过 {skipped}"
            + (f" / 信息 {info}" if info else "")
            + f"（展示最近 {len(shown)} 条，保留 {retention_days} 天）\n"
            f"跳过原因: {', '.join(f'{k}×{v}' for k, v in top_reasons) if top_reasons else '无'}\n"
            f"当前节奏: 最小间隔 {schedule_cfg.min_trigger_interval_minutes} 分钟"
            f"（早晚安{'豁免' if schedule_cfg.min_interval_exempt_greetings else '不豁免'}）"
            f" | 用户冷却 {schedule_cfg.user_cooldown_minutes} 分钟"
            f" | 每日上限 {schedule_cfg.daily_max_speak}"
            f" | 静默 {windows.silence_start}-{windows.silence_end}"
        )
        status_line = self._build_patrol_status_line()
        if status_line:
            header += f"\n{status_line}"
        records: list[dict[str, Any]] = [
            {
                "user_id": "0",
                "nickname": "主动行为日志",
                "segments": [{"type": "text", "content": header}],
            }
        ]

        for entry in shown:
            action = str(entry.get("action") or "")
            trigger_type = str(entry.get("trigger_type") or "")
            reason = str(entry.get("reason") or "unknown")
            if action == "trigger":
                line = (
                    f"[{entry.get('time', '?')}] ✅ 发起触发 · {trigger_type or '?'}\n"
                    f"{entry.get('detail') or ''}"
                )
            elif action == "spoken":
                line = (
                    f"[{entry.get('time', '?')}] 🗣️ 确认发言 · {trigger_type or '?'}\n"
                    f"{entry.get('detail') or ''}"
                )
            elif action == "info":
                line = (
                    f"[{entry.get('time', '?')}] ℹ️ {reason}\n"
                    f"{entry.get('detail') or ''}"
                )
            else:
                label = _DIAG_REASON_LABELS.get(reason, reason)
                line = (
                    f"[{entry.get('time', '?')}] ⏭️ 跳过 · {reason}（{label}）\n"
                    f"{entry.get('detail') or ''}"
                )
            extras: list[str] = []
            minutes_since = entry.get("minutes_since_last_speak")
            if minutes_since is not None:
                extras.append(f"距上次发言 {minutes_since} 分钟")
            if entry.get("budget_used") is not None:
                extras.append(f"预算 {entry.get('budget_used')}/{entry.get('budget_limit', '?')}")
            if entry.get("min_interval_minutes"):
                extras.append(f"最小间隔 {entry.get('min_interval_minutes')} 分钟")
            if entry.get("schedule_nodes") is not None:
                extras.append(f"日程节点 {entry.get('schedule_nodes')} 个")
            if extras:
                line += "\n（" + "，".join(extras) + "）"
            records.append(
                {
                    "user_id": "0",
                    "nickname": "主动行为日志",
                    "segments": [{"type": "text", "content": line}],
                }
            )
        return records

    def _build_patrol_status_line(self) -> str:
        """构造一行运行状态（巡检是否在跑、上次巡检时间、日程来源与节点数）。

        v2.4.3：用于 /mai_diag 的汇总行与"没有记录"时的自诊断——
        直接区分"巡检没启动（stream_id 未解析）"与"刚启动还没巡检过"。
        """

        parts: list[str] = []
        if self._scheduler is not None:
            try:
                status = self._scheduler.patrol_status()
                parts.append(f"巡检={'运行中' if status.get('running') else '未运行'}")
                if status.get("last_tick_at"):
                    parts.append(f"上次巡检={status['last_tick_at']}")
                parts.append(f"间隔={status.get('interval_minutes')}分钟")
                parts.append(f"stream_id={'已就绪' if status.get('stream_id_ready') else '未解析'}")
                parts.append(f"主动开关={'开' if status.get('trigger_enabled') else '关'}")
            except Exception as e:  # noqa: BLE001
                self.ctx.logger.debug(f"读取巡检状态失败: {e}")
        if self._schedule_gen is not None:
            try:
                mode = "外部日程" if self._schedule_gen.is_external_mode() else "本插件生成"
                today = datetime.now().strftime("%Y-%m-%d")
                nodes = len(self._schedule_gen.load_cached_schedule(today))
                parts.append(f"日程来源={mode}（今日节点 {nodes} 个）")
            except Exception as e:  # noqa: BLE001
                self.ctx.logger.debug(f"读取日程状态失败: {e}")
            ext_status = str(getattr(self._external_src, "last_status", "") or "")
            if self._schedule_gen.is_external_mode() and ext_status:
                parts.append(f"外部拉取={ext_status}")
        return ("状态: " + "｜".join(parts)) if parts else ""

    @Command(name="/mai_test", pattern=r"(?<!\S)/mai_test\s*$", description="发送一条测试消息以验证发送通道")
    async def cmd_mai_test(self, **kwargs: Any) -> tuple[bool, str, int]:
        """发送测试消息验证发送通道。优先使用 Command 传入的 stream_id。"""
        if not self._authorize_command(kwargs):
            return True, "没有权限", 2
        stream_id = str(kwargs.get("stream_id", self._cached_stream_id))
        if not stream_id:
            return False, "暂无 stream_id", 2
        try:
            result = await self.ctx.send.text(
                text=f"{self._get_lover_name()}测试消息~ 发送通道正常 ✅",
                stream_id=stream_id,
            )
            if result:
                return True, "测试消息发送成功", 2
            return False, "发送返回 False", 2
        except Exception as e:
            return False, f"发送异常: {e}", 2

    # ── Status / Schedule Reports ──────────────────────────────────────

    def _build_status_report(self) -> str:
        """构造恋人状态报告文本。"""
        if not self._affection_mgr:
            return "麦麦恋人插件尚未初始化完成。"

        level_names: dict[int, str] = {0: "熟悉", 1: "亲密", 2: "热恋"}
        level = self._affection_mgr.level()
        speak_count = self._affection_mgr.today_speak_count()
        daily_max = self.config.schedule.daily_max_speak
        morning_ok = self._affection_mgr.morning_sent_today()
        night_ok = self._affection_mgr.night_sent_today()
        miss_ok = self._affection_mgr.miss_sent_today()
        today_str = datetime.now().strftime("%Y-%m-%d")

        lines: list[str] = [
            f"❤️ 麦麦恋人状态 ({today_str})",
            f"好感度档位: {level} - {level_names.get(level, '未知')}",
            f"今日触发: {speak_count}/{daily_max}",
            f"早安: {'✅已触发' if morning_ok else '❌未触发'}  |  "
            f"晚安: {'✅已触发' if night_ok else '❌未触发'}  |  "
            f"想念: {'✅已触发' if miss_ok else '❌未触发'}",
        ]

        # 日程摘要
        if self._schedule_gen:
            schedule = self._schedule_gen.load_cached_schedule(today_str)
            if schedule:
                lines.append(f"今日日程 ({len(schedule)} 个节点):")
                for node in schedule[:5]:
                    t = node.get("time", "??:??")
                    activity = node.get("activity", "未知")
                    lines.append(f"  [{t}] 🐱 {activity}")
                if len(schedule) > 5:
                    lines.append(f"  ... 还有 {len(schedule) - 5} 个节点")
            else:
                lines.append("今日暂无日程缓存。")

        return "\n".join(lines)

    def _build_schedule_report(self) -> str:
        """构造今日完整日程报告文本。"""
        today_str = datetime.now().strftime("%Y-%m-%d")

        if not self._schedule_gen:
            return "日程生成器未初始化。"

        schedule = self._schedule_gen.load_cached_schedule(today_str)
        if not schedule:
            if self._schedule_gen.is_external_mode():
                return (
                    f"📅 今日 ({today_str}) 暂无日程。\n"
                    "已开启「使用外部日程」，正在等待自主规划插件提供日程"
                    "（请确认其已安装、启用且已生成今日日程）。"
                )
            return f"📅 今日 ({today_str}) 暂无日程缓存。\n可能尚未生成，请等待下次凌晨 {self.config.schedule.generate_hour}:00 自动生成。"

        lines: list[str] = [f"📅 {self._get_lover_name()}今日日程 ({today_str})", ""]
        for node in schedule:
            t = node.get("time", "??:??")
            activity = node.get("activity", "未知")
            lines.append(f"  [{t}] 🐱 {activity}")

        return "\n".join(lines)

    # ── Internal Helpers ───────────────────────────────────────────────

    async def _refresh_bot_config(self) -> None:
        """从主程序配置刷新人设和 bot 昵称缓存。

        读取 ctx.config.get("personality.personality") 和
        ctx.config.get("bot.nickname")，同步给 scheduler（若已创建）。
        """
        try:
            self._cached_personality = await self.ctx.config.get(
                "personality.personality", ""
            )
        except Exception as e:
            self.ctx.logger.warning(f"读取人设配置失败: {e}")
            self._cached_personality = ""

        try:
            self._cached_nickname = await self.ctx.config.get(
                "bot.nickname", ""
            )
        except Exception as e:
            self.ctx.logger.warning(f"读取 bot.nickname 失败: {e}")
            self._cached_nickname = ""

        if self._scheduler is not None:
            self._scheduler.set_personality(self._cached_personality)
        self.ctx.logger.debug(
            f"Bot 配置已刷新: nickname={self._cached_nickname}, "
            f"personality={self._cached_personality[:50]}..."
        )

    def _get_lover_name(self) -> str:
        """获取恋人名称（三级回退）。

        优先级：
        1. 插件配置 plugin.lover_name（非空时直接使用）
        2. 主程序 bot.nickname（配置留空时回退）
        3. "麦麦"（仍为空时的最终默认值）
        """
        try:
            configured = self.config.plugin.lover_name
            if configured and configured.strip():
                return configured.strip()
        except Exception:
            pass
        if self._cached_nickname:
            return self._cached_nickname
        return "麦麦"

    def _check_target_stream(self, stream_id: str) -> bool:
        """校验 stream_id 是否属于白名单目标用户。

        Args:
            stream_id: 待校验的聊天流 ID。

        Returns:
            True 表示是目标用户。
        """
        if not stream_id:
            return False
        return stream_id == self._cached_stream_id

    def _is_authorized_command_user(
        self, user_id: str, is_local_operator: bool = False
    ) -> bool:
        """校验命令触发者是否有权使用 /mai_* 命令（v2.3.1 新增）。

        规则：本机控制台操作员天然放行；其余仅白名单 target_qq 本人可用
        （兼容 ``qq:123456`` 平台前缀写法）。target_qq 为默认值/无效值时
        一律拒绝（默认拒绝），避免任意会话用户调整好感度或读取配置摘要。

        Args:
            user_id: 命令触发者用户 ID（宿主注入）。
            is_local_operator: 是否本机控制台操作员。

        Returns:
            是否有权限。
        """
        if is_local_operator:
            return True

        target_qq = str(self.config.whitelist.target_qq).strip()
        if not target_qq or target_qq in ("0", "123456789"):
            return False

        candidate = str(user_id or "").strip()
        if not candidate:
            return False
        return candidate in (target_qq, f"qq:{target_qq}")

    async def _authorize_command(self, kwargs: dict[str, Any]) -> bool:
        """命令权限守卫：无权限时发送提示并返回 False（调用方直接拦截返回）。

        Args:
            kwargs: Command 处理函数收到的 kwargs（含 user_id / stream_id /
                is_local_operator）。

        Returns:
            True 表示有权限，可继续执行；False 表示已发送拒绝提示。
        """
        if self._is_authorized_command_user(
            str(kwargs.get("user_id", "") or ""),
            bool(kwargs.get("is_local_operator", False)),
        ):
            return True
        stream_id = str(kwargs.get("stream_id", "") or "")
        if stream_id:
            try:
                await self.ctx.send.text(
                    "此命令仅限绑定的恋人用户使用哦~", stream_id
                )
            except Exception as e:
                self.ctx.logger.warning(f"权限提示发送失败: {e}")
        return False

    def _get_data_dir(self) -> str:
        """获取插件数据目录路径。

        SDK 2.6.0+ 使用框架标准持久化目录 ctx.paths.data_dir，
        自动回退旧式自建 data 目录以兼容 SDK 2.5.x。

        Returns:
            数据目录绝对路径。
        """
        paths = getattr(self.ctx, "paths", None)
        if paths is not None:
            return str(paths.data_dir)
        # SDK 2.5.x 降级：在插件源码目录下自建 data/
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

    def _migrate_old_data_dir(self, new_data_dir: str) -> None:
        """将旧式自建 data 目录的数据迁移到框架标准持久化目录。

        SDK 2.6.0+ 推荐使用 ctx.paths.data_dir，旧版本插件在源码目录下
        自建 data/。首次迁移时自动复制旧数据文件（不覆盖已有文件）。

        Args:
            new_data_dir: 新数据目录路径（ctx.paths.data_dir 或降级路径）。
        """
        old_data_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "data"
        )
        if not os.path.isdir(old_data_dir):
            return
        if os.path.abspath(old_data_dir) == os.path.abspath(new_data_dir):
            return  # 同一目录（SDK 2.5.x 降级场景），无需迁移

        import shutil
        migrated = []
        for fname in os.listdir(old_data_dir):
            src = os.path.join(old_data_dir, fname)
            dst = os.path.join(new_data_dir, fname)
            if os.path.isfile(src) and not os.path.exists(dst):
                shutil.copy2(src, dst)
                migrated.append(fname)

        if migrated:
            self.ctx.logger.info(
                f"已从旧目录迁移 {len(migrated)} 个文件: "
                f"{old_data_dir} -> {new_data_dir} ({', '.join(migrated)})"
            )

    async def _resolve_stream_id(self, target_qq: str) -> str:
        """获取目标 QQ 的私聊 stream_id（纯 SDK 路径）。

        使用 SDK 提供的 chat 代理，不直接访问 MaiBot 内部数据库。
        所有层级均做 try/except 降级保护。

        Args:
            target_qq: 目标 QQ 号。

        Returns:
            stream_id 字符串，获取失败返回空字符串。
        """
        # 方法1: get_stream_by_user_id（自动匹配已注册的适配器）
        try:
            stream_info = await self.ctx.chat.get_stream_by_user_id(
                user_id=target_qq
            )
            if isinstance(stream_info, dict) and stream_info.get("stream_id"):
                sid = str(stream_info["stream_id"])
                self.ctx.logger.debug(f"从 get_stream_by_user_id 获取 stream_id: {sid}")
                return sid
        except Exception as e:
            self.ctx.logger.warning(f"get_stream_by_user_id 失败: {e}")

        # 方法2: 遍历 get_private_streams
        try:
            streams = await self.ctx.chat.get_private_streams()
            if isinstance(streams, list):
                for s in streams:
                    if isinstance(s, dict) and str(s.get("user_id", "")) == str(target_qq):
                        sid = str(s.get("stream_id", ""))
                        self.ctx.logger.debug(f"从 get_private_streams(list) 获取 stream_id: {sid}")
                        return sid
            elif isinstance(streams, dict):
                for key, s in streams.items():
                    if isinstance(s, dict) and str(s.get("user_id", "")) == str(target_qq):
                        sid = str(s.get("stream_id", key))
                        self.ctx.logger.debug(f"从 get_private_streams(dict) 获取 stream_id: {sid}")
                        return sid
        except Exception as e:
            self.ctx.logger.error(f"get_private_streams 失败: {e}")

        return ""

    async def _start_scheduler(self) -> None:
        """解析 stream_id 并启动调度器。

        stream_id 解析失败时启动后台重试协程。
        日程生成循环不依赖 stream_id，始终启动。
        """
        if self._scheduler is None:
            self.ctx.logger.error("调度器未初始化，无法启动")
            return

        if self._stream_retry_task is not None:
            self._stream_retry_task.cancel()
            try:
                await asyncio.wait_for(self._stream_retry_task, timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
            self._stream_retry_task = None

        target_qq = str(self.config.whitelist.target_qq)
        if not target_qq or target_qq == "123456789":
            self.ctx.logger.warning("target_qq 未配置，调度器未启动")
            return

        # 先启动 scheduler（日程生成循环不依赖 stream_id）
        self._scheduler.set_target(target_qq, "")  # stream_id 暂空
        await self._scheduler.start()

        # 解析 stream_id
        stream_id = await self._resolve_stream_id(target_qq)
        if stream_id:
            self._cached_stream_id = stream_id
            self._scheduler.set_target(target_qq, stream_id)
            await self._scheduler.start_patrol()
            self.ctx.logger.info(f"目标用户: {target_qq}, stream_id: {stream_id}")
        else:
            if self._scheduler._use_external_schedule():
                self._ctx.logger.warning(
                    f"无法获取 stream_id，外部日程模式已就绪但巡检暂不可用。"
                    f"将在后台快速重试解析..."
                )
            else:
                self._ctx.logger.warning(
                    f"无法获取 stream_id，日程生成已启动但巡检暂不可用。"
                    f"将在后台快速重试解析..."
                )
            self._stream_retry_task = asyncio.create_task(
                self._retry_stream_id(target_qq)
            )

    async def _retry_stream_id(self, target_qq: str) -> None:
        """后台重试解析 stream_id。

        启动阶段使用短退避重试，避免每次重启后固定失效 5 分钟。
        达到 MAX_ATTEMPTS 次后停止，避免无限循环。
        """
        MAX_ATTEMPTS = 60  # 约 1 小时（退避到 60 秒后）
        retry_delays = (5, 10, 20, 30, 60)
        attempt = 0
        while True:
            try:
                delay = retry_delays[min(attempt, len(retry_delays) - 1)]
                await asyncio.sleep(delay)
                stream_id = await self._resolve_stream_id(target_qq)
                if stream_id:
                    self._cached_stream_id = stream_id
                    if self._scheduler is not None:
                        self._scheduler.set_target(target_qq, stream_id)
                        await self._scheduler.start_patrol()
                    self.ctx.logger.info(f"stream_id 重试成功: {stream_id}，巡检循环已就绪")
                    self._stream_retry_task = None
                    return
                else:
                    attempt += 1
                    if attempt >= MAX_ATTEMPTS:
                        self.ctx.logger.warning(
                            f"stream_id 重试已达 {attempt} 次，停止重试。请检查适配器是否正常连接。"
                        )
                        self._stream_retry_task = None
                        return
                    if attempt % 10 == 0:
                        self.ctx.logger.warning(
                            f"stream_id 已重试 {attempt} 次仍未成功"
                        )
                    self.ctx.logger.debug(
                        f"stream_id 重试失败，{retry_delays[min(attempt, len(retry_delays) - 1)]} 秒后再次重试"
                    )
            except asyncio.CancelledError:
                return
            except Exception as e:
                self.ctx.logger.error(f"stream_id 重试异常: {e}")


def create_plugin() -> MaiBotPlugin:
    """MaiBot 插件工厂函数。"""
    return MaiLoverPlugin()
