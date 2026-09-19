"""LLM 调用日志模块（v2.4.0 新增）。

专门记录**由本插件发起**的模型请求的回复内容，并标注：
- 事件来源（schedule_generation / miss_confirm / tool_send_message /
  cateye_screen_describe 等，由调用方在 ``LLMService.generate(event=...)``
  处标注）；
- 发生时间；
- 模型输出（成功记回复文本，失败记错误信息）。

存储形态：按天一个 JSON Lines 文件（``data/llm_logs/llm_calls_YYYY-MM-DD.jsonl``），
一行一条调用记录；保留天数由配置 ``[llm_log] retention_days``（默认 3 天）控制，
过期文件在写入时按小时节流清理 + 插件加载时清理。

查看方式：``/mai_llm_log [days]`` 命令把日志按"每次请求一条消息"合并转发发出。
"""

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("MaiLover.LLMCallLogger")

# 清理节流间隔（秒）：写入路径上最多每小时做一次过期清理
_CLEANUP_INTERVAL_SECONDS = 3600.0

# 单条回复记录的最大字符数（超长截断，防止日志文件被个别超长响应撑爆）
_MAX_RESPONSE_CHARS = 4000


class LLMCallLogger:
    """插件发起的 LLM 调用记录器（JSONL 按天落盘 + 按天过期清理）。"""

    def __init__(
        self,
        data_dir: str | Path,
        enabled: bool = True,
        retention_days: int = 3,
    ) -> None:
        """初始化。

        Args:
            data_dir: 插件数据目录（日志写入其下 ``llm_logs/`` 子目录）。
            enabled: 是否启用记录；关闭时所有方法静默 no-op。
            retention_days: 日志保留天数（超过即清理，最小 1）。
        """
        self._log_dir: Path = Path(data_dir) / "llm_logs"
        self._enabled: bool = bool(enabled)
        self._retention_days: int = max(1, int(retention_days))
        self._last_cleanup_at: float = 0.0

    @property
    def enabled(self) -> bool:
        """是否启用记录。"""
        return self._enabled

    @property
    def retention_days(self) -> int:
        """当前保留天数。"""
        return self._retention_days

    def update_settings(self, enabled: bool, retention_days: int) -> None:
        """热更新开关与保留天数（配置变更时调用）。"""
        self._enabled = bool(enabled)
        self._retention_days = max(1, int(retention_days))

    # ------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------

    def record(
        self,
        event: str,
        model: str,
        success: bool,
        response: str,
        error: str = "",
    ) -> None:
        """记录一次 LLM 调用（成功记回复，失败记错误信息）。

        任何 I/O 异常都静默吞掉——日志绝不影响正常聊天流程。

        Args:
            event: 事件来源标注（如 ``miss_confirm``）。
            model: 使用的模型任务名。
            success: 调用是否成功。
            response: 模型回复文本（失败时可为空）。
            error: 失败时的错误信息。
        """
        if not self._enabled:
            return
        text = str(response or "")
        if len(text) > _MAX_RESPONSE_CHARS:
            text = text[:_MAX_RESPONSE_CHARS] + "…（截断）"
        entry = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "event": str(event or "unspecified"),
            "model": str(model or ""),
            "success": bool(success),
            "response": text,
            "error": str(error or ""),
        }
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            path = self._log_dir / self._filename(datetime.now())
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.debug(f"写入 LLM 调用日志失败（忽略）: {e}")
            return

        # 写入路径上按小时节流做一次过期清理
        now = time.monotonic()
        if now - self._last_cleanup_at >= _CLEANUP_INTERVAL_SECONDS:
            self._last_cleanup_at = now
            self.cleanup()

    # ------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------

    def read_entries(self, days: Optional[int] = None) -> list[dict[str, Any]]:
        """读取最近 N 天的调用记录（按时间升序）。

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
                logger.debug(f"读取 LLM 调用日志失败（跳过 {path.name}）: {e}")
        entries.sort(key=lambda item: str(item.get("time", "")))
        return entries

    # ------------------------------------------------------------
    # 清理
    # ------------------------------------------------------------

    def cleanup(self) -> int:
        """删除超过保留天数的日志文件，返回删除数量。

        按文件名中的日期判定过期（解析失败回退文件修改时间）。
        """
        removed = 0
        if not self._log_dir.exists():
            return 0
        cutoff_date = (datetime.now() - timedelta(days=self._retention_days)).date()
        try:
            candidates = list(self._log_dir.glob("llm_calls_*.jsonl"))
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
                    logger.debug(f"删除过期日志失败（忽略）: {path.name} - {e}")
        if removed:
            logger.info(f"已清理 {removed} 个过期 LLM 调用日志文件（保留 {self._retention_days} 天）")
        return removed

    # ------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------

    @staticmethod
    def _filename(dt: datetime) -> str:
        """某天对应的日志文件名。"""
        return f"llm_calls_{dt.strftime('%Y-%m-%d')}.jsonl"

    @staticmethod
    def _parse_file_date(name: str) -> Optional[Any]:
        """从文件名解析日期；失败返回 None。"""
        stem = name[len("llm_calls_"):-len(".jsonl")]
        try:
            return datetime.strptime(stem, "%Y-%m-%d").date()
        except ValueError:
            return None
