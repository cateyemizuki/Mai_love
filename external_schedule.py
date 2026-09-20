"""外部日程源 - 读取「麦麦自主规划插件」的日程快照。

当配置 ``schedule.use_external_schedule`` 开启时，本插件不再自行生成日程，
改为通过跨插件 API 读取 autonomous_planning_plugin v4 的当日日程：
``ctx.api.call("xuqian13.autonomous-planning-plugin-v4.get_current_activity", chat_id="global")``

数据转换（两套日程系统的兼容层）：
- 自主规划插件的日程是"时间窗口"制：goal.parameters.time_window = "HH:MM-HH:MM"
- 本插件（MaiLover）的日程是"时间点"制：[{time: "HH:MM", activity: str}]
- 转换规则：窗口起点 HH:MM → 节点 time，活动名 goal.name → 节点 activity

快照结构（见自主规划插件 InjectService.get_current_activity_snapshot）::

    {
        "has_activity": bool,
        "activity": {"name", "description", "goal_type", "time_window": "HH:MM-HH:MM"},
        "next_activities": [{"time": "HH:MM", "name": str}, ...],
        "as_of": ISO8601, "timezone": str, "error": None,
    }

注意：快照只含"当前活动 + 未来活动"，不含今天已过时段的活动——
由 ScheduleGenerator.refresh_external_schedule 把每次拉取结果合并进
schedule_cache.json，随时间推进逐步补全全天节点。

调用形态（SDK 约定）：
- 成功 → 返回目标 API 原始返回值（快照 dict）
- 目标插件未加载 / API 不存在 / 目标报错 → 返回 {"success": False, "error": ...}
- 本类把以上两种失败统一折叠为 None，由调用方决定保留旧缓存
"""

import logging
import time
from datetime import datetime
from typing import Any, Optional

# 自主规划插件公开的"当前活动快照" API（manifest id.api_name）
EXTERNAL_SCHEDULE_API = "xuqian13.autonomous-planning-plugin-v4.get_current_activity"

# 拉取节流：该间隔内的重复刷新直接复用上次结果（巡检间隔通常 ≥5 分钟）
FETCH_TTL_SECONDS = 120.0
# 失败日志节流：连续失败时最多每 30 分钟提醒一次，避免刷屏
ERROR_LOG_INTERVAL_SECONDS = 1800.0

_logger = logging.getLogger("MaiLover.ExternalSchedule")


class ExternalScheduleSource:
    """跨插件日程读取器：拉取快照 + 转换为 MaiLover 节点格式。

    带内存级 TTL 节流与失败日志节流；不落盘（持久化由 ScheduleGenerator 负责）。
    """

    def __init__(self, ctx: Any) -> None:
        """初始化。

        Args:
            ctx: MaiBot PluginContext 实例（用于 ctx.api.call 与 ctx.logger）。
        """
        self._ctx: Any = ctx
        self._last_fetch_at: float = 0.0
        self._last_nodes: Optional[list[dict[str, Any]]] = None
        self._last_error_at: float = 0.0
        # v2.4.3：最近一次拉取的状态，供决策日志记录"外部日程到底读到没有"。
        # 取值：unavailable（还没拉过）/ cached（TTL 内复用）/ fresh（拉到节点）/
        #       empty（拉到但对方今日无日程）/ error（拉取失败）
        self._last_status: str = "unavailable"
        self._last_node_count: int = 0

    @property
    def last_status(self) -> str:
        """最近一次拉取的状态（unavailable / cached / fresh / empty / error）。"""

        return self._last_status

    @property
    def last_node_count(self) -> int:
        """最近一次拉取到的节点数量。"""

        return self._last_node_count

    async def get_today_nodes(self, now: datetime, *, force: bool = False) -> Optional[list[dict[str, Any]]]:
        """拉取并转换"当前 + 未来"日程节点。

        Args:
            now: 当前时间（仅用于调用方语义表达，转换以快照为准）。
            force: True 时忽略 TTL 强制拉取。

        Returns:
            成功 → 节点列表（按 time 升序，可能为空列表 = 对方今日暂无日程）；
            拉取失败 / 返回结构异常 → None（调用方应保留旧缓存）。
        """
        now_ts = time.monotonic()
        if not force and self._last_nodes is not None and now_ts - self._last_fetch_at < FETCH_TTL_SECONDS:
            self._last_status = "cached"
            self._last_node_count = len(self._last_nodes)
            return self._last_nodes

        snapshot = await self._fetch_snapshot()
        if snapshot is None:
            self._last_status = "error"
            self._last_node_count = 0
            return None

        self._last_nodes = self.snapshot_to_nodes(snapshot)
        self._last_fetch_at = now_ts
        self._last_status = "fresh" if self._last_nodes else "empty"
        self._last_node_count = len(self._last_nodes)
        return self._last_nodes

    async def _fetch_snapshot(self) -> Optional[dict[str, Any]]:
        """调用跨插件 API 拉取快照；失败折叠为 None（带节流日志）。"""
        try:
            result = await self._ctx.api.call(EXTERNAL_SCHEDULE_API, chat_id="global")
        except Exception as exc:
            self._log_error(f"读取外部日程失败（{EXTERNAL_SCHEDULE_API}）: {exc}")
            return None

        # SDK 约定：目标 API 报错 / 不存在 → {"success": False, "error": ...}
        if not isinstance(result, dict) or result.get("success") is False:
            reason = result.get("error") if isinstance(result, dict) else type(result).__name__
            self._log_error(f"外部日程 API 返回失败: {reason}（请确认自主规划插件已安装并启用）")
            return None
        if "has_activity" not in result:
            self._log_error(f"外部日程 API 返回结构异常，缺少 has_activity 字段: {type(result).__name__}")
            return None
        return result

    def _log_error(self, message: str) -> None:
        """失败日志节流：最多每 ERROR_LOG_INTERVAL_SECONDS 提醒一次。"""
        now_ts = time.monotonic()
        if now_ts - self._last_error_at >= ERROR_LOG_INTERVAL_SECONDS:
            self._last_error_at = now_ts
            self._ctx.logger.warning(message)
        else:
            self._ctx.logger.debug(message)

    @staticmethod
    def snapshot_to_nodes(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        """把自主规划插件的快照转换为 MaiLover 的节点列表。

        - activity.time_window "HH:MM-HH:MM" → 节点 {time: 起点, activity: name}
        - next_activities [{"time", "name"}] → 节点 {time, activity: name}
        - 按 time 去重（同一时间以后写入的为准）并升序排序
        - 跨夜窗口（如 23:00-07:00）的起点就是 23:00，无需特殊处理

        Args:
            snapshot: 自主规划插件 get_current_activity 的返回快照。

        Returns:
            节点列表 [{time, activity}, ...]，可能为空。
        """
        nodes: dict[str, dict[str, Any]] = {}

        activity = snapshot.get("activity")
        if isinstance(activity, dict):
            name = str(activity.get("name") or "").strip()
            start = _window_start(activity.get("time_window"))
            if name and start:
                nodes[start] = {"time": start, "activity": name}

        for item in snapshot.get("next_activities") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            node_time = _normalize_hhmm(item.get("time"))
            if name and node_time:
                nodes[node_time] = {"time": node_time, "activity": name}

        return [nodes[key] for key in sorted(nodes)]


def _window_start(time_window: Any) -> str:
    """从 "HH:MM-HH:MM" 窗口字符串提取起点并校验。"""
    raw = str(time_window or "").strip()
    if "-" not in raw:
        return ""
    return _normalize_hhmm(raw.split("-", 1)[0])


def _normalize_hhmm(value: Any) -> str:
    """校验/规范化 HH:MM 时间字符串；非法返回空串。"""
    raw = str(value or "").strip()
    parts = raw.split(":")
    if len(parts) != 2:
        return ""
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return ""
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return f"{hour:02d}:{minute:02d}"
    return ""
