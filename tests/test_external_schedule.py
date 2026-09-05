"""外部日程模式（use_external_schedule）回归测试。

覆盖：
- ExternalScheduleSource.snapshot_to_nodes 的窗口/节点转换
- ScheduleGenerator 外部模式下的生成短路、缓存清理、合并刷新
- Scheduler 外部模式开关读取的容错
"""

import asyncio
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from Mai_love.config import MaiLoverPluginSettings
from Mai_love.external_schedule import ExternalScheduleSource
from Mai_love.schedule_generator import ScheduleGenerator
from Mai_love.scheduler import Scheduler


def make_settings(external: bool) -> MaiLoverPluginSettings:
    settings = MaiLoverPluginSettings()
    settings.schedule.use_external_schedule = external
    return settings


def make_generator(tmp_path, external: bool, source=None) -> ScheduleGenerator:
    # external=True 且未显式给 source 时，默认给一个"成功返回空日程"的源，
    # 模拟生产环境 plugin.py 恒传入真实 source 的行为
    if external and source is None:
        source = make_source([])
    return ScheduleGenerator(
        str(tmp_path), make_settings(external), None, None, external_source=source
    )


def make_source(nodes) -> ExternalScheduleSource:
    """构造一个固定返回的假外部日程源。"""

    class _FakeSource(ExternalScheduleSource):
        def __init__(self, nodes):
            super().__init__(ctx=None)
            self._nodes = nodes

        async def get_today_nodes(self, now, *, force=False):
            if isinstance(self._nodes, Exception):
                raise self._nodes
            if self._nodes is None:
                return None
            return list(self._nodes)

    return _FakeSource(nodes)


# ---------------------------------------------------------------------------
# 快照 → 节点转换
# ---------------------------------------------------------------------------


def test_snapshot_to_nodes_converts_window_start() -> None:
    snapshot = {
        "has_activity": True,
        "activity": {
            "name": "工作",
            "time_window": "09:00-11:30",
        },
        "next_activities": [
            {"time": "13:00", "name": "午休"},
            {"time": "14:30", "name": "开会"},
        ],
    }
    nodes = ExternalScheduleSource.snapshot_to_nodes(snapshot)
    assert nodes == [
        {"time": "09:00", "activity": "工作"},
        {"time": "13:00", "activity": "午休"},
        {"time": "14:30", "activity": "开会"},
    ]


def test_snapshot_to_nodes_cross_midnight_and_dedup() -> None:
    snapshot = {
        "has_activity": True,
        "activity": {"name": "睡觉", "time_window": "23:00-07:00"},
        "next_activities": [
            {"time": "7:05", "name": "起床"},  # 个位数小时 → 规范化为 07:05
            {"time": "13:00", "name": "午休"},
            {"time": "13:00", "name": "午休（改）"},  # 同一时间去重，后者覆盖
        ],
    }
    nodes = ExternalScheduleSource.snapshot_to_nodes(snapshot)
    assert {"time": "23:00", "activity": "睡觉"} in nodes
    assert {"time": "07:05", "activity": "起床"} in nodes
    assert {"time": "13:00", "activity": "午休（改）"} in nodes
    assert len([n for n in nodes if n["time"] == "13:00"]) == 1


def test_snapshot_to_nodes_ignores_invalid_entries() -> None:
    snapshot = {
        "has_activity": True,
        "activity": {"name": "", "time_window": "bad-window"},
        "next_activities": [
            {"time": "25:00", "name": "非法时间"},
            {"time": "10:00", "name": ""},
            "not-a-dict",
            {"time": "10:00", "name": "有效"},
        ],
    }
    nodes = ExternalScheduleSource.snapshot_to_nodes(snapshot)
    assert nodes == [{"time": "10:00", "activity": "有效"}]


# ---------------------------------------------------------------------------
# ScheduleGenerator 外部模式
# ---------------------------------------------------------------------------


def test_external_mode_requires_source(tmp_path) -> None:
    assert make_generator(tmp_path, external=True).is_external_mode()
    gen = ScheduleGenerator(str(tmp_path), make_settings(True), None, None)
    assert not gen.is_external_mode()  # 无外部源 → 视为未开启
    assert not make_generator(tmp_path, external=False).is_external_mode()


def test_external_mode_blocks_generation_and_clears_cache(tmp_path) -> None:
    gen = make_generator(tmp_path, external=True)
    cache = tmp_path / "schedule_cache.json"
    marker = tmp_path / ".schedule_generated"
    cache.write_text('{"date": "2026-09-05", "nodes": []}', encoding="utf-8")
    marker.write_text("2026-09-05", encoding="utf-8")

    assert gen.is_generated_today("2026-09-05") is True  # 不触发重新生成
    nodes = asyncio.run(gen.generate_daily_schedule("2026-09-05"))
    assert nodes == []
    assert not cache.exists() and not marker.exists()  # 清空已有日程
    assert gen.load_cached_schedule("2026-09-05") == []


def test_local_mode_generation_unaffected(tmp_path) -> None:
    gen = make_generator(tmp_path, external=False)
    assert gen.is_generated_today("2026-09-05") is False


def test_refresh_merges_and_keeps_past_nodes(tmp_path) -> None:
    source = make_source([{"time": "14:00", "activity": "下午茶"}, {"time": "16:00", "activity": "散步"}])
    gen = make_generator(tmp_path, external=True, source=source)

    # 首次刷新：写入外部节点
    asyncio.run(gen.refresh_external_schedule("2026-09-05"))
    assert gen.load_cached_schedule("2026-09-05") == [
        {"time": "14:00", "activity": "下午茶"},
        {"time": "16:00", "activity": "散步"},
    ]

    # 时间推进：外部快照只剩"当前(16:00)+未来"，合并应保留已过时段节点
    source._nodes = [{"time": "16:00", "activity": "散步"}]
    asyncio.run(gen.refresh_external_schedule("2026-09-05"))
    assert gen.load_cached_schedule("2026-09-05") == [
        {"time": "14:00", "activity": "下午茶"},
        {"time": "16:00", "activity": "散步"},
    ]


def test_refresh_failure_keeps_cache(tmp_path) -> None:
    source = make_source([{"time": "10:00", "activity": "晨会"}])
    gen = make_generator(tmp_path, external=True, source=source)
    asyncio.run(gen.refresh_external_schedule("2026-09-05"))

    source._nodes = None  # 模拟拉取失败
    asyncio.run(gen.refresh_external_schedule("2026-09-05"))
    assert gen.load_cached_schedule("2026-09-05") == [{"time": "10:00", "activity": "晨会"}]


def test_refresh_noop_in_local_mode(tmp_path) -> None:
    source = make_source([{"time": "10:00", "activity": "晨会"}])
    gen = make_generator(tmp_path, external=False, source=source)
    asyncio.run(gen.refresh_external_schedule("2026-09-05"))
    assert gen.load_cached_schedule("2026-09-05") == []


# ---------------------------------------------------------------------------
# Scheduler 开关读取
# ---------------------------------------------------------------------------


def test_scheduler_external_flag_fallback() -> None:
    scheduler = object.__new__(Scheduler)
    scheduler._config = SimpleNamespace(
        schedule=SimpleNamespace(user_cooldown_minutes=30)  # 缺 use_external_schedule 字段
    )
    assert scheduler._use_external_schedule() is False

    scheduler._config = SimpleNamespace(schedule=SimpleNamespace(use_external_schedule=True))
    assert scheduler._use_external_schedule() is True


def test_source_error_is_folded_to_none() -> None:
    gen = make_generator(
        Path(tempfile.mkdtemp()),
        external=True,
        source=make_source(RuntimeError("rpc down")),
    )
    asyncio.run(gen.refresh_external_schedule("2026-09-05"))
    assert gen.load_cached_schedule("2026-09-05") == []
    assert datetime.now() is not None  # 保持 import 有意义
