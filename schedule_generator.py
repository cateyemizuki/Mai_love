"""日程生成模块

负责每日日程的生成与缓存：
1. 调用节假日 API 获取日期信息
2. 读取 mai_template.json（麦麦作息骨架）
3. 构造 Prompt（含人设性格）→ LLM 生成日程
4. LLM 失败 → 使用原始模板骨架
5. 存入 schedule_cache.json

v2.0.0 变更：
- 模板文件从 user_template.json 改为 mai_template.json
- generate_daily_schedule 新增 personality 参数
- 新增 get_current_activity 公共方法供 Hook/Tool 查询当前活动

外部日程模式（schedule.use_external_schedule）：
- 开启后本插件不再生成日程，改为读取「麦麦自主规划插件」的日程
  （经 ExternalScheduleSource 转换为 {time, activity} 节点）
- 开启时清空本地日程缓存；快照只含"当前 + 未来"活动，
  refresh_external_schedule 每次把拉取结果合并进缓存，随时间补全全天
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

_logger = logging.getLogger("MaiLover.ScheduleGenerator")

from .config import MaiLoverPluginSettings
from .external_schedule import ExternalScheduleSource
from .holiday_service import HolidayService
from .llm_service import LLMService


class ScheduleGenerator:
    """日程生成器。

    每日凌晨运行，调用 LLM 生成当天的日程节点列表，
    同时缓存到 schedule_cache.json 供调度器使用。

    v2.0.0: 日程节点格式为 {time, activity}，描述麦麦的虚拟日常活动。
    外部日程模式下生成路径全部短路，缓存改由 refresh_external_schedule 维护。
    """

    def __init__(
        self,
        data_dir: str,
        config: MaiLoverPluginSettings,
        llm_service: LLMService,
        holiday_service: HolidayService,
        external_source: Optional[ExternalScheduleSource] = None,
    ) -> None:
        """初始化日程生成器。

        Args:
            data_dir: 数据目录路径（用于缓存文件）。
            config: 插件强类型配置模型（预留给未来功能）。
            llm_service: LLM 服务。
            holiday_service: 节假日服务。
            external_source: 外部日程源（读取自主规划插件）；None = 不支持外部模式。
        """
        self._cache_file: Path = Path(data_dir) / "schedule_cache.json"
        self._marker_file: Path = Path(data_dir) / ".schedule_generated"
        # 模板文件在插件源码根目录（本文件所在目录），不依赖 data_dir
        self._template_file: Path = Path(__file__).parent / "mai_template.json"
        self._config: MaiLoverPluginSettings = config
        self._llm: LLMService = llm_service
        self._holiday: HolidayService = holiday_service
        self._external_source: Optional[ExternalScheduleSource] = external_source

    def is_external_mode(self) -> bool:
        """是否处于外部日程模式（配置开关 + 外部源可用）。"""
        return bool(
            getattr(self._config.schedule, "use_external_schedule", False)
            and self._external_source is not None
        )

    def clear_cached_schedule(self) -> None:
        """清空本地日程缓存与"当日已生成"标记（外部日程模式下使用）。"""
        removed: list[str] = []
        for path in (self._cache_file, self._marker_file):
            try:
                if path.exists():
                    path.unlink()
                    removed.append(path.name)
            except OSError as e:
                _logger.warning(f"删除 {path.name} 失败: {e}")
        if removed:
            _logger.info(f"已清空本地日程文件: {', '.join(removed)}")

    async def refresh_external_schedule(self, date: str) -> dict[str, Any]:
        """外部日程模式：拉取最新日程并合并进本地缓存。

        快照只含"当前 + 未来"活动，因此采用按 time 合并（union）策略：
        旧缓存中今天已过时段的节点保留，新的拉取结果覆盖同时间节点。
        拉取失败时保留现有缓存不动（下次巡检重试）。

        非外部模式下为 no-op，调用方无需判断模式。

        Args:
            date: 日期字符串（YYYY-MM-DD）。

        Returns:
            v2.4.3 起返回状态字典（供主动行为决策日志记录"外部日程到底读到没有"）：
            ``{"mode": "internal"|"external", "result": "noop"|"cached"|"fresh"|"empty"
            |"error"|"exception", "nodes": int, "cached_total": int, "detail": str}``。
        """
        if not self.is_external_mode() or self._external_source is None:
            return {
                "mode": "internal",
                "result": "noop",
                "nodes": 0,
                "cached_total": len(self.load_cached_schedule(date)),
                "detail": "非外部日程模式（本插件自行生成）",
            }

        try:
            nodes = await self._external_source.get_today_nodes(datetime.now())
        except Exception as e:  # noqa: BLE001
            # 外部源异常不中断巡检其余逻辑，保留旧缓存等下次重试
            _logger.warning(f"读取外部日程异常: {e}")
            return {
                "mode": "external",
                "result": "exception",
                "nodes": 0,
                "cached_total": len(self.load_cached_schedule(date)),
                "detail": f"读取外部日程异常: {e}",
            }

        fetch_status = str(getattr(self._external_source, "last_status", "unknown"))
        if nodes is None:
            return {
                "mode": "external",
                "result": "error",
                "nodes": 0,
                "cached_total": len(self.load_cached_schedule(date)),
                "detail": "外部日程拉取失败（对方插件未安装/未启用或 API 报错），保留旧缓存等下次重试",
            }

        if not nodes:
            # 对方今日暂无日程（尚未生成或已清空）：保留旧合并结果，不写入
            return {
                "mode": "external",
                "result": "empty",
                "nodes": 0,
                "cached_total": len(self.load_cached_schedule(date)),
                "detail": "外部日程返回空（对方今日尚未生成日程或时段无安排），保留旧合并结果",
            }

        merged = {str(n.get("time")): n for n in self.load_cached_schedule(date)}
        for node in nodes:
            merged[str(node.get("time"))] = node
        merged_list = [merged[key] for key in sorted(merged)]
        self._save_cache(date, merged_list)
        return {
            "mode": "external",
            "result": fetch_status if fetch_status in {"cached", "fresh"} else "fresh",
            "nodes": len(nodes),
            "cached_total": len(merged_list),
            "detail": f"外部日程拉到 {len(nodes)} 个节点，合并后共 {len(merged_list)} 个",
        }

    async def generate_daily_schedule(
        self, date: str, personality: str = "", lover_name: str = "麦麦"
    ) -> list[dict[str, Any]]:
        """生成当日日程。

        流程：
        1. 调用节假日 API
        2. 读取 mai_template.json
        3. 构造 Prompt（含人设性格）→ LLM 生成
        4. LLM 失败 → 使用 mai_template.json 原始骨架
        5. 存入 schedule_cache.json

        外部日程模式下短路：清空本地缓存并返回空列表，绝不生成。

        Args:
            date: 日期字符串（YYYY-MM-DD）。
            personality: 麦麦人设性格文本（从 ctx.config.get 读取）。
            lover_name: 恋人名称（从 bot.nickname 读取，默认"麦麦"）。

        Returns:
            日程节点列表 [{time, activity}, ...]。
        """
        if self.is_external_mode():
            # 外部日程模式：清空已有日程且不再生成（双保险，调度器已跳过生成循环）
            self.clear_cached_schedule()
            return []

        # 1. 获取节假日信息
        holiday_info = await self._holiday.get_holiday_info(date)

        # 2. 读取麦麦作息模板
        template_text = self._read_template()

        # 3. 调用 LLM 生成日程（传入人设性格）
        nodes: list[dict[str, Any]] = []
        try:
            nodes = await self._llm.generate_schedule(
                date, holiday_info, template_text, personality, lover_name
            )
        except Exception:
            # LLM 生成异常，将在下一步使用降级骨架
            pass

        # 4. LLM 失败 → 使用原始模板骨架
        if not nodes:
            nodes = self._build_fallback_schedule(date)

        # 5. 缓存到文件
        self._save_cache(date, nodes)

        # 6. 写入当日已生成标记，防止短时间重启重复触发 LLM 生成
        self._mark_generated(date)

        return nodes

    def load_cached_schedule(self, date: str) -> list[dict[str, Any]]:
        """读取缓存的日程。

        日期不匹配则返回空列表。

        Args:
            date: 日期字符串（YYYY-MM-DD）。

        Returns:
            日程节点列表，或空列表。
        """
        if not self._cache_file.exists():
            return []
        try:
            with open(self._cache_file, "r", encoding="utf-8") as f:
                cache = json.load(f)
            if cache.get("date") == date and cache.get("nodes"):
                return cache["nodes"]
        except (json.JSONDecodeError, IOError):
            pass
        return []

    def is_generated_today(self, date: str) -> bool:
        """检查当日日程是否已成功生成。

        同时检查标记文件和缓存文件，任一有效即视为已生成。
        标记文件的引入是为了防止短时间内多次 stop/start
        导致缓存文件未写回时重复触发 LLM 生成。

        外部日程模式下恒返回 True（不生成，"已生成"由外部插件负责）。

        Args:
            date: 日期字符串（YYYY-MM-DD）。

        Returns:
            True 表示当日已生成，无需重新生成。
        """
        if self.is_external_mode():
            return True
        # 优先检查标记文件（比缓存文件更可靠：原子写入，不受覆盖影响）
        if self._marker_file.exists():
            try:
                marker = self._marker_file.read_text(encoding="utf-8").strip()
                if marker == date:
                    return True
            except IOError:
                pass
        # 回退检查缓存文件
        return bool(self.load_cached_schedule(date))

    def _mark_generated(self, date: str) -> None:
        """写入当日已生成标记。

        Args:
            date: 日期字符串（YYYY-MM-DD）。
        """
        try:
            self._marker_file.parent.mkdir(parents=True, exist_ok=True)
            self._marker_file.write_text(date, encoding="utf-8")
        except IOError as e:
            _logger.warning(f"写入 .schedule_generated 标记失败: {e}（将回退缓存检查）")

    def find_current_activity(self, now: datetime) -> Optional[str]:
        """查找当前时间点麦麦正在做的活动（可区分"无日程"）。

        查找逻辑：
        1. 加载今日日程缓存
        2. 在所有 time <= 当前时间的节点中，取最后一个的 activity
        3. 无日程缓存 / 没有已开始的节点 → 返回 None

        兼容旧格式节点：若无 activity 字段则跳过该节点。

        v2.3.0: 与 :meth:`get_current_activity` 的区别在于"无日程"返回 None
        而非占位文案——外部日程模式（自主规划插件 v4.7 起无睡眠时段不生成
        日程）与凌晨日切后（对方当日日程尚未生成）都不应再注入日程状态，
        由调用方决定跳过注入。

        Args:
            now: 当前时间。

        Returns:
            活动描述字符串；无日程 / 无已开始节点时返回 None。
        """
        today_str = now.strftime("%Y-%m-%d")
        schedule = self.load_cached_schedule(today_str)
        if not schedule:
            return None

        now_minutes = now.hour * 60 + now.minute
        current_activity: Optional[str] = None

        for node in schedule:
            node_time = str(node.get("time", ""))
            if not node_time:
                continue
            node_minutes = self._time_to_minutes(node_time)
            if node_minutes is None:
                continue
            # 节点时间 <= 当前时间 → 麦麦可能正在做这件事
            if node_minutes <= now_minutes:
                activity = str(node.get("activity", ""))
                if activity:
                    current_activity = activity
            else:
                # 节点时间 > 当前时间 → 后续节点还没开始，停止遍历
                break

        return current_activity

    def get_current_activity(self, now: datetime) -> str:
        """查找当前时间点麦麦正在做的活动。

        无日程或无匹配节点时返回占位文案 "今天还没有安排"。
        需要区分"无日程"的场景（如 planner 注入）请改用
        :meth:`find_current_activity`。

        Args:
            now: 当前时间。

        Returns:
            活动描述字符串。
        """
        activity = self.find_current_activity(now)
        if activity:
            return activity
        return "今天还没有安排"

    def _read_template(self) -> str:
        """读取 mai_template.json 并返回格式化文本。

        Returns:
            格式化后的模板文本。
        """
        if not self._template_file.exists():
            return "{}"
        try:
            with open(self._template_file, "r", encoding="utf-8") as f:
                return f.read()
        except IOError:
            return "{}"

    def _save_cache(self, date: str, nodes: list[dict[str, Any]]) -> None:
        """保存日程到缓存文件。

        Args:
            date: 日期字符串。
            nodes: 日程节点列表。
        """
        try:
            self._cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._cache_file, "w", encoding="utf-8") as f:
                json.dump(
                    {"date": date, "nodes": nodes}, f, ensure_ascii=False, indent=2
                )
        except IOError:
            pass

    def _build_fallback_schedule(self, date: str) -> list[dict[str, Any]]:
        """使用原始模板构建降级日程。

        根据日期是工作日还是周末选择合适的模板。
        mai_template.json 的节点已是 {time, activity} 格式，无需额外处理。

        Args:
            date: 日期字符串。

        Returns:
            日程节点列表 [{time, activity}, ...]。
        """
        try:
            dt = datetime.strptime(date, "%Y-%m-%d")
            is_weekend = dt.weekday() >= 5
        except ValueError:
            is_weekend = False

        template = self._load_template_json()
        if is_weekend and "weekend" in template:
            return template["weekend"]
        if "workday" in template:
            return template["workday"]
        return []

    def _load_template_json(self) -> dict[str, Any]:
        """加载 mai_template.json 为字典。

        Returns:
            模板字典，含 'workday' 和 'weekend' 键。
        """
        if not self._template_file.exists():
            return {}
        try:
            with open(self._template_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}

    @staticmethod
    def _time_to_minutes(time_str: str) -> Optional[int]:
        """将 HH:MM 时间字符串转换为当天分钟数。

        Args:
            time_str: 时间字符串（如 "08:30"）。

        Returns:
            分钟数（如 510），解析失败返回 None。
        """
        try:
            parts = time_str.strip().split(":")
            if len(parts) != 2:
                return None
            hour = int(parts[0])
            minute = int(parts[1])
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return hour * 60 + minute
        except (ValueError, IndexError):
            pass
        return None
