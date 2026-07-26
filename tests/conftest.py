import sys
from types import ModuleType

from pydantic import BaseModel, Field


sdk = ModuleType("maibot_sdk")
sdk.PluginConfigBase = BaseModel
sdk.Field = Field
# 常用组件装饰器 stub（防 import 报错）
sdk.API = type("API", (), {"__call__": lambda self, *a, **k: lambda f: f})()
sdk.Command = type("Command", (), {"__call__": lambda self, *a, **k: lambda f: f})()
sdk.HookHandler = type("HookHandler", (), {"__call__": lambda self, *a, **k: lambda f: f})()
sdk.MaiBotPlugin = BaseModel
sdk.Tool = type("Tool", (), {"__call__": lambda self, *a, **k: lambda f: f})()
sys.modules.setdefault("maibot_sdk", sdk)

# maibot_sdk.types stub（HookMode/HookOrder/ErrorPolicy 等）
types_mod = ModuleType("maibot_sdk.types")


class HookMode:
    BLOCKING = "blocking"
    OBSERVE = "observe"


class HookOrder:
    EARLY = "early"
    NORMAL = "normal"
    LATE = "late"


class ErrorPolicy:
    ABORT = "abort"
    SKIP = "skip"
    LOG = "log"


types_mod.HookMode = HookMode
types_mod.HookOrder = HookOrder
types_mod.ErrorPolicy = ErrorPolicy
sys.modules.setdefault("maibot_sdk.types", types_mod)
