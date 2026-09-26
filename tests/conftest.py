"""本地离线测试夹具（stub SDK，不随插件发布）。

职责：
1. 用 stub 替换 ``maibot_sdk``（本目录测试不依赖真实宿主）；
2. 把插件目录映射为 ``Mai_love`` 包（目录名含连字符，无法直接 import）。
"""

import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# 1) maibot_sdk stub
# ---------------------------------------------------------------------------

sdk = ModuleType("maibot_sdk")
sdk.PluginConfigBase = BaseModel
sdk.Field = Field


def _component_decorator(*args: Any, **kwargs: Any):
    """组件装饰器 stub：把声明参数挂到 ``__maibot_component_info__`` 供测试读取。"""

    def _wrap(fn):
        fn.__maibot_component_info__ = dict(kwargs)
        return fn

    return _wrap


sdk.API = _component_decorator
sdk.Command = _component_decorator
sdk.HookHandler = _component_decorator
sdk.Tool = _component_decorator
sdk.EventHandler = _component_decorator
sdk.HomeCard = _component_decorator
sdk.MessageGateway = _component_decorator


class MaiBotPluginStub:
    """MaiBotPlugin 基类 stub：提供 ``ctx`` property（真实 SDK 为 _ctx 的 property）。"""

    def __init__(self) -> None:
        self._ctx: Any = None

    @property
    def ctx(self) -> Any:
        return self._ctx


sdk.MaiBotPlugin = MaiBotPluginStub
sys.modules.setdefault("maibot_sdk", sdk)

# maibot_sdk.types stub（HookMode/HookOrder/ErrorPolicy/Tool 参数类型等）
types_mod = ModuleType("maibot_sdk.types")

from types import SimpleNamespace

types_mod.HookMode = SimpleNamespace(BLOCKING="blocking", OBSERVE="observe")
types_mod.HookOrder = SimpleNamespace(EARLY="early", NORMAL="normal", LATE="late")
types_mod.ErrorPolicy = SimpleNamespace(ABORT="abort", SKIP="skip", LOG="log")


class ToolParamType:
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    FLOAT = "float"
    BOOLEAN = "boolean"
    ARRAY = "array"
    OBJECT = "object"


class ToolParameterInfo:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


class EventType:
    ON_START = "on_start"
    ON_STOP = "on_stop"


types_mod.ToolParamType = ToolParamType
types_mod.ToolParameterInfo = ToolParameterInfo
types_mod.EventType = EventType
sys.modules.setdefault("maibot_sdk.types", types_mod)

# ---------------------------------------------------------------------------
# 2) 插件目录 → ``Mai_love`` 包别名
#    （发布目录名为 maibot-community_mai-love，含连字符不能作为包名导入）
# ---------------------------------------------------------------------------

_PLUGIN_DIR = Path(__file__).resolve().parent.parent
_pkg = ModuleType("Mai_love")
_pkg.__path__ = [str(_PLUGIN_DIR)]
sys.modules.setdefault("Mai_love", _pkg)
