"""恋人电脑联动 - 读取「cateye 统一连接插件」的电脑状态（v2.4.0 新增）。

当配置 ``[cateye] enabled`` 开启时，想念回复与早安/晚安触发前通过跨插件
API 访问 cateye.connect-hub，获知恋人在电脑上干什么：

- 先查 ``cateye.connect-hub.status``：``connected=false``（或 hub 未安装、
  调用失败）→ 视为「恋人的电脑没开」，直接把这句话给 LLM；
- 已连接 → 调 ``cateye.connect-hub.screenshot`` 截屏（可配模糊半径），
  把截图交给视觉模型（``vlm`` 任务）转成一句话描述，拼进触发提示词；
- 截图或描述失败 → 降级为「恋人的电脑开着，但没看清TA在干什么」。

调用形态（SDK 约定）：``ctx.api.call`` 目标报错/不存在时返回
``{"success": False, "error": ...}``；hub 的 call 形 API 返回
``{"result", "error"}``（无 success 字段）。本类统一折叠为安全降级，
任何异常都不外抛——电脑状态属于锦上添花，绝不能阻塞主动消息触发。
"""

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Optional

# cateye 统一连接插件（cateye.connect-hub）公共 API 全名
HUB_API_STATUS = "cateye.connect-hub.status"
HUB_API_SCREENSHOT = "cateye.connect-hub.screenshot"

# 连接状态 TTL 缓存：该间隔内的重复查询直接复用上次结果
_STATUS_TTL_SECONDS = 60.0

logger = logging.getLogger("MaiLover.CateyeClient")

# 未连接（含 hub 未安装）时给 LLM 的固定文案
TEXT_COMPUTER_OFFLINE = "恋人的电脑没开"
# 已连接但截图/描述失败时的降级文案
TEXT_COMPUTER_BLURRED = "恋人的电脑开着，但没看清TA在干什么"


class CateyeClient:
    """恋人电脑读取器：在线判定 + 截图 + 视觉描述，带降级与超时保护。"""

    def __init__(self, ctx: Any, config: Any, llm_service: Any) -> None:
        """初始化。

        Args:
            ctx: MaiBot PluginContext 实例（ctx.api.call / ctx.logger）。
            config: 插件强类型配置（读 ``config.cateye`` 段）。
            llm_service: LLMService 实例（截图视觉理解用）。
        """
        self._ctx: Any = ctx
        self._config: Any = config
        self._llm: Any = llm_service
        self._connected_cache: Optional[bool] = None
        self._connected_cached_at: float = 0.0

    # ------------------------------------------------------------
    # 对外入口
    # ------------------------------------------------------------

    def is_enabled(self) -> bool:
        """功能开关（配置缺字段时安全回退 False）。"""
        return bool(getattr(getattr(self._config, "cateye", None), "enabled", False))

    async def get_computer_context(self, now: datetime) -> str:
        """获取「恋人电脑」上下文文案，供触发提示词拼接。

        Returns:
            功能关闭 → ""（调用方跳过拼接）；
            未连接 → 固定文案「恋人的电脑没开」；
            已连接 → 截图描述或降级文案；整体超时/异常 → ""。
        """
        if not self.is_enabled():
            return ""
        try:
            timeout = float(getattr(self._config.cateye, "timeout_seconds", 20.0))
            return await asyncio.wait_for(
                self._build_context(now), timeout=max(5.0, timeout)
            )
        except asyncio.TimeoutError:
            logger.info("查看恋人电脑超时，本轮跳过电脑状态注入")
            return ""
        except Exception as e:  # noqa: BLE001 —— 绝不阻塞主动触发
            logger.warning(f"查看恋人电脑异常（忽略）: {e}")
            return ""

    # ------------------------------------------------------------
    # 内部流程
    # ------------------------------------------------------------

    async def _build_context(self, now: datetime) -> str:
        """实际的状态获取流程（已在功能开关通过后调用）。"""
        if not await self.is_connected():
            logger.info("恋人电脑未连接（或 cateye 插件不可用），按「电脑没开」处理")
            return f"（{TEXT_COMPUTER_OFFLINE}）"

        image_b64 = await self._take_screenshot()
        if not image_b64:
            logger.info("恋人电脑截图失败，降级为「开着但没看清」")
            return f"（{TEXT_COMPUTER_BLURRED}）"

        description = await self._describe_screenshot(image_b64)
        if not description:
            logger.info("屏幕截图视觉理解失败（宿主可能不支持图片输入），降级处理")
            return f"（{TEXT_COMPUTER_BLURRED}）"

        logger.info(f"恋人电脑状态：{description}")
        return f"（看了一眼恋人的电脑：{description}）"

    async def is_connected(self) -> bool:
        """本地客户端是否在线（status API，带 TTL 缓存）。

        hub 未安装 / 调用异常 / 字段缺失一律视为未连接。
        """
        now_ts = time.monotonic()
        if (
            self._connected_cache is not None
            and now_ts - self._connected_cached_at < _STATUS_TTL_SECONDS
        ):
            return self._connected_cache

        connected = False
        try:
            resp = await self._ctx.api.call(HUB_API_STATUS)
            connected = bool(isinstance(resp, dict) and resp.get("connected"))
        except Exception as e:  # noqa: BLE001
            logger.debug(f"查询 cateye 连接状态失败（视为未连接）: {e}")

        self._connected_cache = connected
        self._connected_cached_at = now_ts
        return connected

    async def _take_screenshot(self) -> str:
        """截取恋人电脑屏幕，成功返回 base64 PNG，失败返回空串。"""
        cateye_cfg = getattr(self._config, "cateye", None)
        blur = int(getattr(cateye_cfg, "screenshot_blur", 0) or 0)
        user_id = str(getattr(getattr(self._config, "whitelist", None), "target_qq", "") or "")
        try:
            resp = await self._ctx.api.call(
                HUB_API_SCREENSHOT, user_id=user_id, blur=blur
            )
        except Exception as e:  # noqa: BLE001
            logger.debug(f"cateye 截图调用异常: {e}")
            return ""
        if not isinstance(resp, dict) or not resp.get("success"):
            error = resp.get("error") if isinstance(resp, dict) else type(resp).__name__
            logger.debug(f"cateye 截图失败: {error}")
            return ""
        return str(resp.get("image_base64", "") or "")

    async def _describe_screenshot(self, image_b64: str) -> str:
        """用视觉模型把截图转成一句话描述，失败返回空串。"""
        cateye_cfg = getattr(self._config, "cateye", None)
        prompt = str(
            getattr(cateye_cfg, "describe_prompt", "") or ""
        )
        if self._llm is None:
            return ""
        return await self._llm.describe_image(image_b64, prompt)
