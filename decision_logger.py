"""主动行为决策日志模块（v2.4.2 新增）。

记录**每一轮巡检的决策结论**：是否处于静默时段、距上次主动发言多久、最小间隔/用户冷却是否
通过、概率掷点结果、当日预算余量，以及最终动作（发言 / 跳过 + 原因）。

为什么要单独一个日志：主动私聊本身**不调用插件的 LLM**（它只把 ``intent``/``reason`` 文本交给
宿主 ``maisaka.proactive.trigger``，真正的 planner/replyer 调用由宿主发起），所以
``llm_logger`` 里永远看不到"主动发言"这件事。"为什么又发了""今天怎么没发"这类问题，
只能由本日志回答——而不是去翻宿主主日志里孤零零的一行 ``B级触发: 日常巡检``。

存储形态：按天一个 JSON Lines 文件（``proactive_logs/decisions_YYYY-MM-DD.jsonl``），
一行一条；保留天数由 ``[proactive_log] retention_days``（默认 3 天）控制，
过期文件在写入时按小时节流清理 + 插件加载时清理。

查看方式：``/mai_diag [days]`` 命令，把最近的决策按"每轮巡检一条记录"合并转发发出。
"""

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("MaiLover.ProactiveDecisionLogger")

# 清理节流间隔（秒）：写入路径上最多每小时做一次过期清理
_CLEANUP_INTERVAL_SECONDS = 3600.0

# 单条 detail 的最大字符数（超长截断）
_MAX_DETAIL_CHARS = 400

# 允许出现在记录里的动作值
#: 插件发起了一次主动触发（已入队，等 planner 决定说什么）
ACTION_TRIGGER = "trigger"
#: 这一轮没有触发（含原因）
ACTION_SKIP = "skip"
#: planner 确认真的生成并发送了回复（对最近一次触发的回执，v2.4.3）
ACTION_SPOKEN = "spoken"
#: 只作说明的信息行（例如外部日程拉取结果，v2.4.3）
ACTION_INFO = "info"


class ProactiveDecisionLogger:
    """主动行为决策记录器（JSONL 按天落盘 + 按天过期清理）。"""

    def __init__(
        self,
        data_dir: str | Path,
        enabled: bool = True,
        retention_days: int = 3,
        record_skips: bool = True,
    ) -> None:
        """初始化。

        Args:
            data_dir: 插件数据目录（日志写入其下 ``proactive_logs/`` 子目录）。
            enabled: 是否启用记录；关闭时所有方法静默 no-op。
            retention_days: 日志保留天数（超过即清理，最小 1）。
            record_skips: 是否连"这一轮没发（含原因）"也记录。关闭则只记真正触发的轮次。
        """

        self._log_dir: Path = Path(data_dir) / "proactive_logs"
        self._enabled: bool = bool(enabled)
        self._retention_days: int = max(1, int(retention_days))
        self._record_skips: bool = bool(record_skips)
        self._last_cleanup_at: float = 0.0

    # ------------------------------------------------------------
    # 属性与热更新
    # ------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """是否启用记录。"""

        return self._enabled

    @property
    def retention_days(self) -> int:
        """当前保留天数。"""

        return self._retention_days

    @property
    def record_skips(self) -> bool:
        """是否记录"跳过"的轮次。"""

        return self._record_skips

    def update_settings(
        self,
        enabled: bool,
        retention_days: int,
        record_skips: Optional[bool] = None,
    ) -> None:
        """热更新开关、保留天数与"是否记录跳过轮次"（配置变更时调用）。"""

        self._enabled = bool(enabled)
        self._retention_days = max(1, int(retention_days))
        if record_skips is not None:
            self._record_skips = bool(record_skips)

    # ------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------

    def record(
        self,
        *,
        action: str,
        reason: str = "",
        trigger_type: str = "",
        detail: str = "",
        **fields: Any,
    ) -> None:
        """记录一条主动行为事件。

        任何 I/O 异常都静默吞掉——日志绝不影响正常聊天流程。

        Args:
            action: 取值见模块常量：``trigger``（发起主动触发）/ ``skip``（本轮没触发）/
                ``spoken``（planner 确认真的发出去了，v2.4.3）/ ``info``（说明性信息，
                如外部日程拉取结果，v2.4.3）。
            reason: 机器可读的原因键，如 ``silence`` / ``min_interval`` / ``cooldown`` /
                ``budget`` / ``dice`` / ``invalid_time_config`` / ``no_candidate`` /
                ``external_schedule_fresh`` / ``reply_confirmed`` / ``tool_send_message``。
            trigger_type: 触发类型，如 ``morning`` / ``night`` / ``miss`` / ``activity`` /
                ``daily`` / ``tool`` / ``schedule_source``。
            detail: 人类可读的一句话说明。
            **fields: 追加到记录里的决策输入（静默状态、冷却、掷点、预算等）。
        """

        if not self._enabled:
            return
        normalized_action = str(action or "").strip() or ACTION_SKIP
        if normalized_action == ACTION_SKIP and not self._record_skips:
            return

        text = str(detail or "")
        if len(text) > _MAX_DETAIL_CHARS:
            text = text[:_MAX_DETAIL_CHARS] + "…（截断）"

        entry: dict[str, Any] = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "action": normalized_action,
            "trigger_type": str(trigger_type or ""),
            "reason": str(reason or ""),
            "detail": text,
        }
        for key, value in fields.items():
            if value is None:
                continue
            entry[str(key)] = value

        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            path = self._log_dir / self._filename(datetime.now())
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except (OSError, TypeError, ValueError) as e:
            logger.debug(f"写入主动行为决策日志失败（忽略）: {e}")
            return

        now = time.monotonic()
        if now - self._last_cleanup_at >= _CLEANUP_INTERVAL_SECONDS:
            self._last_cleanup_at = now
            self.cleanup()

    # ------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------

    def read_entries(self, days: Optional[int] = None) -> list[dict[str, Any]]:
        """读取最近 N 天的决策记录（按时间升序）。

        Args:
            days: 天数；None = 全部保留期内（retention_days）。

        Returns:
            条目列表（结构与 :meth:`record` 写入的一致），损坏行跳过。
        """

        span = self._retention_days if days is None else max(1, int(days))
        entries: list[dict[str, Any]] = []
        seen_files: set[Path] = set()
        base = datetime.now()
        for offset in range(span):
            path = self._log_dir / self._filename(base - timedelta(days=offset))
            if path in seen_files or not path.exists():
                continue
            seen_files.add(path)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            item = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(item, dict):
                            entries.append(item)
            except OSError as e:
                logger.debug(f"读取主动行为决策日志失败（跳过 {path.name}）: {e}")
        entries.sort(key=lambda item: str(item.get("time", "")))
        return entries

    # ------------------------------------------------------------
    # 清理
    # ------------------------------------------------------------

    def cleanup(self) -> int:
        """删除超过保留天数的日志文件，返回删除数量。"""

        removed = 0
        if not self._log_dir.exists():
            return 0
        cutoff_date = (datetime.now() - timedelta(days=self._retention_days)).date()
        try:
            candidates = list(self._log_dir.glob("decisions_*.jsonl"))
        except OSError:
            return 0
        for path in candidates:
            file_date = self._parse_file_date(path.name)
            if file_date is None:
                try:
                    file_date = datetime.fromtimestamp(path.stat().st_mtime).date()
                except OSError:
                    continue
            if file_date < cutoff_date:
                try:
                    path.unlink()
                    removed += 1
                except OSError as e:
                    logger.debug(f"删除过期决策日志失败（忽略）: {path.name} - {e}")
        if removed:
            logger.info(f"已清理 {removed} 个过期主动行为决策日志文件（保留 {self._retention_days} 天）")
        return removed

    # ------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------

    @staticmethod
    def _filename(dt: datetime) -> str:
        """某天对应的日志文件名。"""

        return f"decisions_{dt.strftime('%Y-%m-%d')}.jsonl"

    @staticmethod
    def _parse_file_date(name: str) -> Optional[Any]:
        """从文件名解析日期；失败返回 None。"""

        stem = name[len("decisions_"):-len(".jsonl")]
        try:
            return datetime.strptime(stem, "%Y-%m-%d").date()
        except ValueError:
            return None
