"""节假日服务模块

负责获取指定日期的节假日/工作日信息。

v2.5.0 重写（此前的实现实际上从未生效，详见下）：
- 旧实现读的是 ``data["holiday"]["type"]``，而 timor.tech 的类型其实在
  ``data["type"]["type"]``，``holiday`` 里没有 ``type`` 键 → 永远取不到 →
  每次都被本地"按星期几判断"兜底。结果就是：国庆节显示"工作日"、
  调休补班的周六显示"周末休息日"。
- 旧实现也完全没处理 ``type == 3``（调休补班）。

现在的取数顺序（多级降级）：

1. **整年放假安排表**（主源 timor.tech ``/api/holiday/year/{year}``）：
   一次请求拿到全年节假日 + 调休补班安排，含 ``holiday=false`` 的补班日；
   表落盘缓存（``<data_dir>/holiday_cache.json``），默认 3 天刷新一次，
   之后离线也能给出正确结果（重启后依然可用）。
2. **备源 jiejiariapi**（``/v1/holidays/{year}``）：字段为 ``isOffDay``。
3. **按天 API**（timor.tech ``/api/holiday/info/{date}``）：整年表拉不到时使用。
4. **本地星期几判断**：全部网络失败时的兜底（``HOLIDAY_FALLBACK_WEEKDAYS``）。

整年表里查不到的日期 = 没有特殊安排，按星期几判断（工作日 / 周末休息日）。
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from typing import Any, Optional

import httpx

from .config import MaiLoverPluginSettings
from .constants import HOLIDAY_FALLBACK_WEEKDAYS


class HolidayService:
    """节假日信息服务（整年安排表 + 磁盘缓存 + 多级降级）。"""

    #: 整年安排表（主源，含调休补班）
    YEAR_API_TEMPLATE: str = "https://timor.tech/api/holiday/year/{year}"
    #: 整年安排表（备源）
    YEAR_API_FALLBACK_TEMPLATE: str = "https://api.jiejiariapi.com/v1/holidays/{year}"
    #: 按天查询（整年表不可用时的降级）
    INFO_API_TEMPLATE: str = "https://timor.tech/api/holiday/info/{date}"

    #: 整年表的磁盘缓存刷新间隔（秒）：3 天。安排表一年只发布几次，
    #: 3 天足够捕捉"新一年安排公布"这类变化，又不会频繁打网络。
    TABLE_TTL_SECONDS: float = 3 * 24 * 3600
    #: 内存缓存保留的日期条数
    MEMORY_CACHE_LIMIT: int = 7
    #: 网络请求超时（秒）
    TIMEOUT_SECONDS: float = 6.0

    #: 调休补班日的中文描述（备源拿不到"XX前/后补班"细名时使用）
    MAKEUP_FALLBACK_TEXT: str = "调休工作日（需要上班）"

    def __init__(
        self,
        config: MaiLoverPluginSettings,
        data_dir: Optional[str] = None,
    ) -> None:
        """初始化节假日服务。

        Args:
            config: 插件强类型配置模型。
            data_dir: 插件数据目录；提供时把整年安排表落盘缓存，
                重启与离线场景仍可给出正确结果。
        """
        self._config: MaiLoverPluginSettings = config
        self._data_dir: Optional[str] = data_dir
        #: date -> 描述文本
        self._cache: dict[str, str] = {}
        #: year -> {date: {"off": bool, "name": str, "kind": str}}
        self._tables: dict[int, dict[str, dict[str, Any]]] = {}
        #: year -> 上次拉取时间（epoch 秒）
        self._table_fetched_at: dict[int, float] = {}
        self._load_disk_cache()

    # ── 对外接口 ────────────────────────────────────────────────────────

    async def get_holiday_info(self, date: str) -> str:
        """获取指定日期的节假日/工作日信息。

        Args:
            date: 日期字符串（YYYY-MM-DD）。

        Returns:
            中文描述，如 "工作日" / "周末休息日" / "国庆节假期" /
            "调休工作日（国庆节后补班）"。
        """
        if date in self._cache:
            return self._cache[date]

        dt = self._parse_date(date)
        info: Optional[str] = None

        if dt is not None:
            table = await self._ensure_year_table(dt.year)
            if table is not None:
                info = self._describe(dt, table.get(date))

        if info is None:
            entry = await self._call_info_api(date)
            if entry is not None:
                dt_for_entry = dt if dt is not None else self._parse_date(date)
                info = self._describe(dt_for_entry, entry) if dt_for_entry else None

        if info is None:
            info = self._local_judge(date)

        self._cache[date] = info
        if len(self._cache) > self.MEMORY_CACHE_LIMIT:
            oldest = min(self._cache.keys())
            del self._cache[oldest]
        return info

    async def refresh(self, year: int) -> bool:
        """强制刷新某一年的安排表（忽略 TTL）。供排错/手动刷新使用。"""
        table = await self._fetch_year_table(year)
        if table is None:
            return False
        self._tables[year] = table
        self._table_fetched_at[year] = time.time()
        self._cache.clear()
        self._save_disk_cache()
        return True

    def table_size(self, year: int) -> int:
        """返回某年已缓存的特殊日期条数（0 表示没有该年数据）。"""
        return len(self._tables.get(year, {}))

    # ── 磁盘缓存 ────────────────────────────────────────────────────────

    @property
    def _cache_file(self) -> Optional[str]:
        if not self._data_dir:
            return None
        return os.path.join(self._data_dir, "holiday_cache.json")

    def _load_disk_cache(self) -> None:
        """读取磁盘上的整年安排表缓存（失败静默忽略）。"""
        path = self._cache_file
        if not path or not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            tables = payload.get("tables")
            if not isinstance(tables, dict):
                return
            for raw_year, entry in tables.items():
                try:
                    year = int(raw_year)
                except (TypeError, ValueError):
                    continue
                if not isinstance(entry, dict):
                    continue
                days = entry.get("days")
                if not isinstance(days, dict):
                    continue
                self._tables[year] = {
                    str(key): dict(value)
                    for key, value in days.items()
                    if isinstance(value, dict)
                }
                try:
                    self._table_fetched_at[year] = float(entry.get("fetched_at") or 0.0)
                except (TypeError, ValueError):
                    self._table_fetched_at[year] = 0.0
        except Exception:
            # 缓存损坏不影响功能：当作没有缓存，走网络
            return

    def _save_disk_cache(self) -> None:
        """把整年安排表写入磁盘（失败静默忽略，绝不影响主流程）。"""
        path = self._cache_file
        if not path:
            return
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            payload = {
                "version": 1,
                "tables": {
                    str(year): {
                        "fetched_at": self._table_fetched_at.get(year, 0.0),
                        "days": days,
                    }
                    for year, days in self._tables.items()
                },
            }
            tmp_path = f"{path}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=1)
            os.replace(tmp_path, path)
        except Exception:
            return

    # ── 整年表 ──────────────────────────────────────────────────────────

    async def _ensure_year_table(self, year: int) -> Optional[dict[str, dict[str, Any]]]:
        """取某一年的安排表；本地有新鲜缓存则直接用。

        Returns:
            年份 -> {日期: 条目}；完全拿不到时返回 None（由调用方降级）。
        """
        cached = self._tables.get(year)
        fetched_at = self._table_fetched_at.get(year, 0.0)
        if cached is not None and (time.time() - fetched_at) < self.TABLE_TTL_SECONDS:
            return cached

        fresh = await self._fetch_year_table(year)
        if fresh is not None:
            self._tables[year] = fresh
            self._table_fetched_at[year] = time.time()
            self._save_disk_cache()
            return fresh

        # 拉取失败：过期的旧表也远好于"按星期几瞎猜"
        return cached

    async def _fetch_year_table(self, year: int) -> Optional[dict[str, dict[str, Any]]]:
        """联网拉取整年安排（主源失败自动换备源）。"""
        table = await self._fetch_year_table_timor(year)
        if table:
            return table
        table = await self._fetch_year_table_jiejiariapi(year)
        if table:
            return table
        return None

    async def _fetch_year_table_timor(self, year: int) -> Optional[dict[str, dict[str, Any]]]:
        """主源：timor.tech 整年安排表（含调休补班 ``holiday=false``）。"""
        data = await self._get_json(self.YEAR_API_TEMPLATE.format(year=year))
        if not isinstance(data, dict) or data.get("code") != 0:
            return None
        holidays = data.get("holiday")
        if not isinstance(holidays, dict) or not holidays:
            return None

        table: dict[str, dict[str, Any]] = {}
        for key, raw in holidays.items():
            if not isinstance(raw, dict):
                continue
            date = str(raw.get("date") or "").strip() or self._expand_key(str(key), year)
            if not date:
                continue
            name = str(raw.get("name") or "").strip()
            if raw.get("holiday") is True:
                table[date] = {"off": True, "name": name, "kind": "holiday"}
            else:
                table[date] = {"off": False, "name": name, "kind": "makeup"}
        return table or None

    async def _fetch_year_table_jiejiariapi(self, year: int) -> Optional[dict[str, dict[str, Any]]]:
        """备源：jiejiariapi 整年安排表（``isOffDay`` 表示是否放假）。

        备源只给"这是哪个假期"（如 ``国庆节``），没有"XX前/后补班"这类细名，
        也把"小年"之类的非假日条目一并列出，因此这里：
        - 只把 ``isOffDay=False`` **且落在周末**的条目录为调休（周末要上班 → 必然是调休）；
        - 工作日的非假日条目（小年等）直接忽略，避免误判成调休。
        文案里再用 ``（{name}补班）`` 补齐，不编造"前/后补班"的方向。
        """
        data = await self._get_json(self.YEAR_API_FALLBACK_TEMPLATE.format(year=year))
        if not isinstance(data, dict) or not data:
            return None

        table: dict[str, dict[str, Any]] = {}
        for key, raw in data.items():
            if not isinstance(raw, dict):
                continue
            date = str(raw.get("date") or key).strip()
            dt = self._parse_date(date)
            if dt is None:
                continue
            name = str(raw.get("name") or "").strip()
            is_off = raw.get("isOffDay")
            if is_off is True:
                table[date] = {"off": True, "name": name, "kind": "holiday"}
            elif is_off is False and dt.weekday() >= 5:
                # 周末却标注为上班 → 调休补班
                table[date] = {"off": False, "name": name, "kind": "makeup"}
        return table or None

    async def _call_info_api(self, date: str) -> Optional[dict[str, Any]]:
        """按天查询（timor.tech）。

        正确读法（旧实现读错层级导致永不生效）：
        - 类型在 ``data["type"]["type"]``：0=工作日 1=周末 2=节日 3=调休；
        - ``data["holiday"]`` 里是细节（``holiday`` 布尔 + ``name`` + ``target``）。
        """
        data = await self._get_json(self.INFO_API_TEMPLATE.format(date=date))
        if not isinstance(data, dict) or data.get("code") != 0:
            return None

        holiday = data.get("holiday")
        if isinstance(holiday, dict):
            name = str(holiday.get("name") or "").strip()
            if holiday.get("holiday") is True:
                return {"off": True, "name": name, "kind": "holiday"}
            return {"off": False, "name": name, "kind": "makeup"}

        type_info = data.get("type")
        if not isinstance(type_info, dict):
            return None
        type_value = type_info.get("type")
        type_name = str(type_info.get("name") or "").strip()
        if type_value == 1:  # 周末
            return {"off": True, "name": "", "kind": "weekend"}
        if type_value == 2:  # 节日
            return {"off": True, "name": type_name, "kind": "holiday"}
        if type_value == 3:  # 调休（补班/放假）
            return {"off": False, "name": type_name, "kind": "makeup"}
        if type_value == 0:  # 工作日
            return {"off": False, "name": "", "kind": "workday"}
        return None

    async def _get_json(self, url: str) -> Optional[Any]:
        """带超时与异常兜底的 GET JSON。"""
        try:
            async with httpx.AsyncClient(timeout=self.TIMEOUT_SECONDS) as client:
                resp = await client.get(
                    url, headers={"User-Agent": "MaiLover/2.5.0"}
                )
                if resp.status_code != 200:
                    return None
                return resp.json()
        except Exception:
            return None

    # ── 文本生成 ────────────────────────────────────────────────────────

    def _describe(self, dt: datetime, entry: Optional[dict[str, Any]]) -> str:
        """把条目转成给 LLM 看的中文描述。"""
        if not entry:
            return self._local_judge(dt.strftime("%Y-%m-%d"))

        off = bool(entry.get("off"))
        name = str(entry.get("name") or "").strip()
        kind = str(entry.get("kind") or "")

        if off:
            if name:
                return f"{name}假期"
            return "休息日"

        if kind == "makeup":
            if "补班" in name or "调休" in name:
                return f"调休工作日（{name}）"
            if name:
                return f"调休工作日（{name}补班）"
            return self.MAKEUP_FALLBACK_TEXT
        if dt.weekday() >= 5:
            # 表里明确说了不放假，但又是周末 → 只能是调休
            return self.MAKEUP_FALLBACK_TEXT
        return "工作日"

    @staticmethod
    def _local_judge(date: str) -> str:
        """本地根据星期几判断工作日/休息日（最后的兜底）。

        Args:
            date: 日期字符串（YYYY-MM-DD）。

        Returns:
            "工作日" 或 "周末休息日"。
        """
        dt = HolidayService._parse_date(date)
        if dt is None:
            return "工作日"
        if dt.weekday() in HOLIDAY_FALLBACK_WEEKDAYS:
            return "工作日"
        return "周末休息日"

    # ── 小工具 ──────────────────────────────────────────────────────────

    @staticmethod
    def _parse_date(date: str) -> Optional[datetime]:
        try:
            return datetime.strptime(str(date).strip(), "%Y-%m-%d")
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _expand_key(key: str, year: int) -> str:
        """把 ``MM-DD`` 形式的键补成 ``YYYY-MM-DD``。"""
        normalized = key.strip()
        if not normalized:
            return ""
        if len(normalized) == 5 and normalized[2] == "-":
            return f"{year}-{normalized}"
        return normalized
