"""麦麦恋人（MaiLover）WebUI 配置模型。

提供符合 MaiBot PluginConfigBase 规范的配置定义，
支持 WebUI 表单渲染与多语言说明。
"""

from __future__ import annotations

from typing import Any, ClassVar, Dict, Literal, Optional

from maibot_sdk import Field, PluginConfigBase
from pydantic import field_validator

from .constants import (
    MISS_CONFIRM_PROMPT_DEFAULT,
    MISS_REASON_PROMPT_DEFAULT,
    SCREEN_DESCRIBE_PROMPT_DEFAULT,
)


def _schema_i18n(
    *,
    label_en: str,
    label_ja: str,
    hint_en: Optional[str] = None,
    hint_ja: Optional[str] = None,
    placeholder_en: Optional[str] = None,
    placeholder_ja: Optional[str] = None,
) -> Dict[str, Dict[str, str]]:
    """构造 WebUI 配置项多语言说明。"""

    i18n: Dict[str, Dict[str, str]] = {
        "en_US": {"label": label_en},
        "ja_JP": {"label": label_ja},
    }
    if hint_en is not None:
        i18n["en_US"]["hint"] = hint_en
    if hint_ja is not None:
        i18n["ja_JP"]["hint"] = hint_ja
    if placeholder_en is not None:
        i18n["en_US"]["placeholder"] = placeholder_en
    if placeholder_ja is not None:
        i18n["ja_JP"]["placeholder"] = placeholder_ja
    return i18n


# ---------------------------------------------------------------------------
# 插件总开关
# ---------------------------------------------------------------------------

CONFIG_SCHEMA_VERSION = "2.4.0"


class PluginConfig(PluginConfigBase):
    """控制插件是否启用。关闭后插件完全静默，不主动找你说话。"""

    __ui_label__: ClassVar[str] = "插件设置"
    __ui_order__: ClassVar[int] = 0

    config_version: str = Field(
        default=CONFIG_SCHEMA_VERSION,
        description="配置 schema 版本，请勿手动修改。",
        json_schema_extra={
            "disabled": True,
            "hidden": True,
            "label": "配置版本",
            "i18n": _schema_i18n(label_en="Config version", label_ja="設定バージョン"),
            "order": 99,
        },
    )
    llm_model: Literal["replyer", "planner", "utils"] = Field(
        default="planner",
        description="生成日程和回复使用的模型任务名。replyer=回复模型，planner=规划模型，utils=工具模型。",
        json_schema_extra={
            "hint": "对应 MaiBot model_config 的模型任务名（utils/replyer/planner…）。"
                    "planner 通用性好，replyer 回复更自然。",
            "i18n": _schema_i18n(
                label_en="LLM Model Task",
                label_ja="LLMモデル",
                hint_en="Matches model task names configured in MaiBot. planner is versatile, replyer is more natural.",
                hint_ja="MaiBotで設定したモデルタスク名に対応。plannerは汎用的、replyerはより自然な返信。",
            ),
            "label": "LLM 模型任务名",
            "order": 1,
        },
    )
    lover_name: str = Field(
        default="麦麦",
        description="恋人的名字。用于日程生成、Tool 描述和括号小剧场中的称呼。留空时回退到主程序 bot.nickname，仍为空则用'麦麦'。",
        json_schema_extra={
            "hint": "恋怎么称呼自己。填名字就用该名字；留空则自动跟随主程序 bot 昵称；都为空时默认'麦麦'。",
            "i18n": _schema_i18n(
                label_en="Lover Name",
                label_ja="恋人の名前",
                hint_en="How she refers to herself. Leave empty to use bot.nickname; falls back to '麦麦' if both are empty.",
                hint_ja="恋人が自分を呼ぶ名前。空欄なら bot.nickname を使用、どちらも空なら'麦麦'になります。",
            ),
            "label": "恋人名称",
            "order": 0,
        },
    )
    enabled: bool = Field(
        default=True,
        description="插件总开关。开启后麦麦才会主动找你说话；关闭则完全静默，不检查日程、不主动发消息。热更新即时生效。",
        json_schema_extra={
            "hint": "总闸：打开麦麦才会主动找你说话。关闭后一切主动行为（早安晚安、日程、想念）全部停止。热更新即时生效。",
            "i18n": _schema_i18n(
                label_en="Enable Plugin",
                label_ja="プラグインを有効化",
                hint_en="Master switch. When off, MaiMai is completely silent — no proactive messages.",
                hint_ja="マスタースイッチ。オフにすると麦麦は完全に沈黙し、能動的メッセージも送信しません。",
            ),
            "label": "启用插件",
            "order": 0,
        },
    )

    @field_validator("llm_model", mode="before")
    @classmethod
    def _normalize_llm_model(cls, value: Any) -> Any:
        """兼容旧版非法任务名：``reply`` 不是宿主合法任务名（正确为 ``replyer``）。

        旧配置里写了 ``reply`` 的用户升级后自动映射，避免 Literal 校验失败
        或运行时"未找到名为 reply 的模型配置"导致全部生成静默降级。
        """
        if isinstance(value, str) and value.strip().lower() == "reply":
            return "replyer"
        return value


# ---------------------------------------------------------------------------
# 白名单配置
# ---------------------------------------------------------------------------


class WhitelistConfig(PluginConfigBase):
    """绑定唯一的 QQ 号。只有这个号码的私聊消息会触发麦麦的所有主动逻辑。"""

    __ui_label__: ClassVar[str] = "白名单设置"
    __ui_order__: ClassVar[int] = 1

    target_qq: int = Field(
        default=123456789,
        description="只有这个号的私聊会激活麦麦的所有功能。填你自己的 QQ 号，修改后需要重启插件。",
        json_schema_extra={
            "hint": "填你的 QQ 号。只对这一个号生效（私聊），群聊完全不管。改完需要重启插件或 MaiBot。",
            "i18n": _schema_i18n(
                label_en="Target QQ Number",
                label_ja="対象QQ番号",
                hint_en="MaiMai only talks to this QQ number in private chat. Group chats are unaffected. Restart required after change.",
                hint_ja="麦麦はこのQQ番号とのプライベートチャットでのみ話します。グループチャットは影響を受けません。変更後は再起動が必要です。",
                placeholder_en="123456789",
                placeholder_ja="123456789",
            ),
            "label": "目标 QQ 号",
            "order": 0,
            "placeholder": "123456789",
        },
    )

    @field_validator("target_qq", mode="before")
    @classmethod
    def _normalize_target_qq(cls, value: Any) -> int:
        if isinstance(value, str):
            try:
                return int(value.strip())
            except ValueError:
                return 0
        if isinstance(value, (int, float)):
            return int(value)
        return 0


# ---------------------------------------------------------------------------
# 日程与巡检配置
# ---------------------------------------------------------------------------


class ScheduleConfig(PluginConfigBase):
    """控制麦麦什么时候检查、一天最多说几句话、说话冷却多久。"""

    __ui_label__: ClassVar[str] = "调度设置"
    __ui_order__: ClassVar[int] = 2

    generate_hour: int = Field(
        default=3,
        description="每天几点自动生成麦麦今天的活动安排。0~23 之间的整数。",
        json_schema_extra={
            "hint": "默认 3 点（凌晨生成，不影响白天）。改成 0~23 之间的整数。",
            "i18n": _schema_i18n(
                label_en="Schedule Generation Hour",
                label_ja="スケジュール生成時刻",
                hint_en="Which hour (0-23) to generate the daily schedule. Default 3 AM, during off-peak time.",
                hint_ja="毎日のスケジュールを生成する時間（0-23）。デフォルトは午前3時、オフピーク時間です。",
            ),
            "label": "日程生成时间（时）",
            "order": 0,
        },
    )
    check_interval_minutes: int = Field(
        default=10,
        description="麦麦每隔多少分钟看一眼现在该不该找你说话。建议 5~10 分钟。",
        json_schema_extra={
            "hint": "值越小麦麦反应越快。默认 10 分钟即可。",
            "i18n": _schema_i18n(
                label_en="Check Interval (min)",
                label_ja="チェック間隔（分）",
                hint_en="How often MaiMai checks if it's time to speak. Lower = faster response. Default 5.",
                hint_ja="麦麦が話すタイミングをチェックする頻度です。低いほど反応が速くなります。",
            ),
            "label": "巡检间隔（分钟）",
            "order": 1,
        },
    )
    daily_max_speak: int = Field(
        default=5,
        description="麦麦一天最多主动找你几次。到了上限就不找了，连早安晚安也不发。0=完全禁言。",
        json_schema_extra={
            "hint": "含早安晚安。想话多调大（8~12），想安静调小（2~3）。0=完全静音。",
            "i18n": _schema_i18n(
                label_en="Daily Max Messages",
                label_ja="1日の最大メッセージ数",
                hint_en="Hard cap on proactive messages per day, including morning/night. Increase for more chatty, decrease for quieter. 0 = total silence.",
                hint_ja="1日の能動的メッセージの上限（おはよう/おやすみを含む）。おしゃべりにしたい場合は増やし、静かにしたい場合は減らします。0 = 完全無音。",
            ),
            "label": "每日发言上限",
            "order": 2,
        },
    )
    user_cooldown_minutes: int = Field(
        default=30,
        description="你刚发完消息后，麦麦多久之内不会主动找你。比如你刚说了句话，如果设为 30 分钟，这 30 分钟内麦麦不会突然蹦出来打扰你。早安晚安不受此限制。",
        json_schema_extra={
            "hint": "冷却期（分钟）。你刚发完消息后麦麦会闭嘴这多久。避免「刚说完就又来」的骚扰感。早安晚安无视冷却。",
            "i18n": _schema_i18n(
                label_en="User Cooldown (min)",
                label_ja="ユーザークールダウン（分）",
                hint_en="After you send a message, MaiMai stays quiet for this many minutes to avoid feeling intrusive. Morning/night greetings ignore cooldown.",
                hint_ja="あなたがメッセージを送った後、麦麦がこの分数だけ静かにします。押し付けがましさを避けるためです。おはよう/おやすみはクールダウンを無視します。",
            ),
            "label": "用户冷却时间（分钟）",
            "order": 3,
        },
    )
    proactive_trigger_enabled: bool = Field(
        default=True,
        description="麦麦会不会主动找你说话。关掉后麦麦就乖乖等你先说话，不会主动来烦你。日程表照常生成。",
        json_schema_extra={
            "hint": "关闭后早安晚安、想念、日常全部停，但日程照常生成。适合想安静一阵。",
            "i18n": _schema_i18n(
                label_en="Proactive Trigger",
                label_ja="プロアクティブトリガー",
                hint_en="When off, schedule still generates but MaiMai won't proactively message you.",
                hint_ja="オフ時、スケジュールは生成されますが麦麦は能動的にメッセージを送信しません。",
            ),
            "label": "主动触发开关",
            "order": 4,
        },
    )
    use_external_schedule: bool = Field(
        default=False,
        description="使用外部日程：开启后不再自行生成日程，改为通过插件 API 读取「麦麦自主规划插件」"
                    "（xuqian13.autonomous-planning-plugin-v4）生成的日程；开启时会清空本插件已生成的"
                    "日程缓存，关闭后恢复自动生成。需要两个插件同时安装。",
        json_schema_extra={
            "hint": "开启 = 日程来源改为自主规划插件，本插件清空缓存且不再生成；关闭 = 恢复本插件自动生成。",
            "i18n": _schema_i18n(
                label_en="Use External Schedule",
                label_ja="外部スケジュールを使用",
                hint_en="Read the daily schedule from the autonomous planning plugin instead of generating one; local schedule cache is cleared and generation stops while enabled.",
                hint_ja="オンにすると自主計画プラグインのスケジュールを読み込み、ローカルの生成を停止してキャッシュを消去します。",
            ),
            "label": "使用外部日程",
            "order": 5,
        },
    )

    @field_validator("generate_hour", mode="before")
    @classmethod
    def _normalize_hour(cls, value: Any) -> int:
        return _normalize_int_in_range(value, 3, 0, 23)

    @field_validator("check_interval_minutes", mode="before")
    @classmethod
    def _normalize_interval(cls, value: Any) -> int:
        return _normalize_int_in_range(value, 5, 0, 60)

    @field_validator("daily_max_speak", mode="before")
    @classmethod
    def _normalize_max_speak(cls, value: Any) -> int:
        return _normalize_int_in_range(value, 5, 0, 100)

    @field_validator("user_cooldown_minutes", mode="before")
    @classmethod
    def _normalize_cooldown(cls, value: Any) -> int:
        return _normalize_int_in_range(value, 5, 0, 60)


# ---------------------------------------------------------------------------
# 概率配置
# ---------------------------------------------------------------------------


class ProbabilityConfig(PluginConfigBase):
    """控制麦麦主动找你说话的概率。三个独立概率分别控制日常、想念和日程节点。"""

    __ui_label__: ClassVar[str] = "概率设置"
    __ui_order__: ClassVar[int] = 3

    default_speak_rate: float = Field(
        default=0.6,
        description="麦麦日常主动找你说话的概率。0.6=60% 概率会来搭话。调高更黏人，调低更高冷。0=只在她有事（早晚安/想念/到点活动）时才说话。",
        json_schema_extra={
            "hint": "0.0~1.0，越高越话多。",
            "i18n": _schema_i18n(
                label_en="Default Speak Rate",
                label_ja="デフォルト発話率",
                hint_en="Probability MaiMai chats with you. Higher = more talkative. 0 = only morning/night/missing-you.",
                hint_ja="麦麦が日常的に話す確率です。高いほどおしゃべりになります。0 = おはよう/おやすみ/「会いたい」のみ。",
            ),
            "label": "常规发言概率",
            "order": 0,
        },
    )
    miss_speak_rate: float = Field(
        default=0.5,
        description="你很久没理她时，麦麦跑来说想你的概率。默认 50%，基本一定会说。每天最多 1 次。",
        json_schema_extra={
            "hint": "0.0~1.0，默认 50%。",
            "i18n": _schema_i18n(
                label_en="Missing-You Rate",
                label_ja="「会いたい」発話率",
                hint_en="Probability MaiMai says she misses you when you've been silent. 50% default, once per day.",
                hint_ja="あなたが長く沈黙しているとき麦麦が「会いたい」と言う確率です。デフォルト 50%、1 日 1 回。",
            ),
            "label": "想念触发概率",
            "order": 1,
        },
    )
    activity_trigger_rate: float = Field(
        default=0.6,
        description="日程节点到点时，麦麦分享她在干嘛的概率。比如 14:00 安排了晒太阳，到点了按这个概率决定要不要告诉你。",
        json_schema_extra={
            "hint": "0.0~1.0，默认 60% 比较自然。",
            "i18n": _schema_i18n(
                label_en="Activity Trigger Rate",
                label_ja="アクティビティトリガー率",
                hint_en="Probability MaiMai shares her activity at schedule nodes. 60% feels natural.",
                hint_ja="スケジュールノードで麦麦が活動を共有する確率です。60%が自然です。",
            ),
            "label": "日程节点触发概率",
            "order": 2,
        },
    )

    @field_validator("default_speak_rate", "miss_speak_rate", "activity_trigger_rate", mode="before")
    @classmethod
    def _normalize_rate(cls, value: Any) -> float:
        if isinstance(value, str):
            try:
                value = float(value.strip())
            except (ValueError, TypeError):
                return 0.5
        if isinstance(value, (int, float)):
            return max(0.0, min(1.0, float(value)))
        return 0.5


# ---------------------------------------------------------------------------
# 时间窗口配置
# ---------------------------------------------------------------------------


class TimeWindowsConfig(PluginConfigBase):
    """设定早安晚安的时间范围，以及想念机制的触发区间、提示词与 LLM 驳回。"""

    __ui_label__: ClassVar[str] = "时间窗口"
    __ui_order__: ClassVar[int] = 4

    morning_start: str = Field(
        default="06:00",
        description="早安时间窗开始。在这个时间段内麦麦会主动跟你说早安。格式 HH:MM。",
        json_schema_extra={
            "hint": "格式 HH:MM，如 06:00 表示早上 6 点开始。",
            "i18n": _schema_i18n(
                label_en="Morning Start",
                label_ja="おはよう開始",
                hint_en="Start of morning window (HH:MM).",
                hint_ja="おはようの時間枠開始（HH:MM）。",
                placeholder_en="06:00",
                placeholder_ja="06:00",
            ),
            "label": "早安开始时间",
            "order": 0,
            "placeholder": "06:00",
        },
    )
    morning_end: str = Field(
        default="09:00",
        description="早安时间窗结束。",
        json_schema_extra={
            "hint": "格式 HH:MM。",
            "i18n": _schema_i18n(
                label_en="Morning End",
                label_ja="おはよう終了",
                hint_en="End of morning window (HH:MM).",
                hint_ja="おはようの時間枠終了（HH:MM）。",
                placeholder_en="09:00",
                placeholder_ja="09:00",
            ),
            "label": "早安结束时间",
            "order": 1,
            "placeholder": "09:00",
        },
    )
    night_start: str = Field(
        default="22:00",
        description="晚安时间窗开始。在这个时间段内麦麦会主动跟你说晚安。格式 HH:MM。",
        json_schema_extra={
            "hint": "格式 HH:MM，如 22:00。",
            "i18n": _schema_i18n(
                label_en="Night Start",
                label_ja="おやすみ開始",
                hint_en="Start of night window (HH:MM).",
                hint_ja="おやすみの時間枠開始（HH:MM）。",
                placeholder_en="22:00",
                placeholder_ja="22:00",
            ),
            "label": "晚安开始时间",
            "order": 2,
            "placeholder": "22:00",
        },
    )
    night_end: str = Field(
        default="23:59",
        description="晚安时间窗结束。",
        json_schema_extra={
            "hint": "格式 HH:MM。",
            "i18n": _schema_i18n(
                label_en="Night End",
                label_ja="おやすみ終了",
                hint_en="End of night window (HH:MM).",
                hint_ja="おやすみの時間枠終了（HH:MM）。",
                placeholder_en="23:59",
                placeholder_ja="23:59",
            ),
            "label": "晚安结束时间",
            "order": 3,
            "placeholder": "23:59",
        },
    )
    miss_trigger_hours_min: float = Field(
        default=4.0,
        description="想念触发区间的下限（小时）：沉默不足这个时间绝不会触发想念。",
        json_schema_extra={
            "hint": "小时，可填小数（如 4.5）。配合上限构成触发区间。",
            "i18n": _schema_i18n(
                label_en="Miss Trigger Min (hours)",
                label_ja="「会いたい」最小トリガー（時間）",
                hint_en="Never triggers before this many hours of silence.",
                hint_ja="この時間未満の沈黙では「会いたい」は発生しません。",
            ),
            "label": "想念触发下限（小时）",
            "order": 4,
        },
    )
    miss_trigger_hours_max: float = Field(
        default=8.0,
        description="想念触发区间的上限（小时）：沉默超过这个时间后每次巡检都会满足时长条件。"
                    "区间内每次巡检随机取一个阈值，沉默越久越容易触发，行为不再像定时炸弹。",
        json_schema_extra={
            "hint": "小时。上限应 ≥ 下限；写反时自动交换。",
            "i18n": _schema_i18n(
                label_en="Miss Trigger Max (hours)",
                label_ja="「会いたい」最大トリガー（時間）",
                hint_en="After this many hours the duration condition always passes.",
                hint_ja="この時間を超えると条件は常に満たされます。",
            ),
            "label": "想念触发上限（小时）",
            "order": 5,
        },
    )
    miss_llm_check_enabled: bool = Field(
        default=True,
        description="想念触发前先让 LLM 以角色身份判断此刻主动说'想你了'是否自然，"
                    "不自然则本轮驳回（30 分钟后才允许再次判断）。关闭则退回纯概率触发。",
        json_schema_extra={
            "hint": "开启 = 触发前 LLM 把关，减少'硬接话题'的突兀感；关闭 = 达到条件就按概率直接触发。",
            "i18n": _schema_i18n(
                label_en="Miss LLM Check",
                label_ja="「会いたい」LLM確認",
                hint_en="Ask the LLM in-character whether reaching out now feels natural; rejected checks are retried after 30 minutes.",
                hint_ja="発話前に LLM が自然かどうかを判断します。却下された場合は 30 分後に再試行します。",
            ),
            "label": "想念触发前 LLM 把关",
            "order": 6,
        },
    )
    miss_reason_prompt: str = Field(
        default=MISS_REASON_PROMPT_DEFAULT,
        description="想念触发时传给 planner 的提示文本（reason）。可用占位符：{lover_name}、{hours}。",
        json_schema_extra={
            "hint": "触发后 planner 据此自主发挥；占位符 {hours}=沉默小时数，{lover_name}=恋人名。",
            "i18n": _schema_i18n(
                label_en="Miss Reason Prompt",
                label_ja="「会いたい」理由プロンプト",
                hint_en="Sent to the planner when a miss trigger fires. Placeholders: {lover_name}, {hours}.",
                hint_ja="トリガー時に planner へ渡すテキスト。プレースホルダー: {lover_name}, {hours}。",
            ),
            "label": "想念触发提示词（触发后）",
            "order": 7,
            "rows": 3,
        },
    )
    miss_confirm_prompt: str = Field(
        default=MISS_CONFIRM_PROMPT_DEFAULT,
        description="LLM 把关用的提示词模板（回复 Y 才触发、N 驳回）。可用占位符："
                    "{lover_name}、{personality}、{current_time}、{hours}、{activity_context}。",
        json_schema_extra={
            "hint": "{activity_context}=当前活动（无日程时为空串）；改完保存即热更新生效。",
            "i18n": _schema_i18n(
                label_en="Miss Confirm Prompt",
                label_ja="「会いたい」確認プロンプト",
                hint_en="Gate prompt template; LLM must answer Y to proceed. Placeholders: {lover_name}, {personality}, {current_time}, {hours}, {activity_context}.",
                hint_ja="LLM が Y と答えたときのみ発火。プレースホルダー: {lover_name} など。",
            ),
            "label": "想念把关提示词（触发前）",
            "order": 8,
            "rows": 5,
        },
    )
    @field_validator("miss_trigger_hours_min", mode="before")
    @classmethod
    def _normalize_miss_hours_min(cls, value: Any) -> float:
        return _normalize_float_in_range(value, 4.0, 0.5, 72.0)

    @field_validator("miss_trigger_hours_max", mode="before")
    @classmethod
    def _normalize_miss_hours_max(cls, value: Any) -> float:
        return _normalize_float_in_range(value, 8.0, 0.5, 72.0)

    silence_start: str = Field(
        default="00:00",
        description="静默时段开始。在这个时间之后麦麦不主动找你说话，让你好好休息。格式 HH:MM。",
        json_schema_extra={
            "hint": "比如设 00:00，表示零点后麦麦闭嘴。配合 silence_end 一起用。格式 HH:MM。",
            "i18n": _schema_i18n(
                label_en="Silence Start",
                label_ja="サイレンス開始",
                hint_en="Start of quiet hours. MaiMai won't proactively message after this time. Format HH:MM.",
                hint_ja="サイレンス時間の開始。この時間以降、麦麦は能動的にメッセージを送りません。形式 HH:MM。",
                placeholder_en="00:00",
                placeholder_ja="00:00",
            ),
            "label": "静默开始时间",
            "order": 5,
            "placeholder": "00:00",
        },
    )
    silence_end: str = Field(
        default="08:00",
        description="静默时段结束。过了这个时间麦麦恢复正常，可以主动找你。格式 HH:MM。",
        json_schema_extra={
            "hint": "比如设 08:00，表示早上 8 点后麦麦恢复话痨。格式 HH:MM。",
            "i18n": _schema_i18n(
                label_en="Silence End",
                label_ja="サイレンス終了",
                hint_en="End of quiet hours. MaiMai resumes proactive messaging after this time. Format HH:MM.",
                hint_ja="サイレンス時間の終了。この時間以降、麦麦は能動的メッセージを再開します。形式 HH:MM。",
                placeholder_en="08:00",
                placeholder_ja="08:00",
            ),
            "label": "静默结束时间",
            "order": 6,
            "placeholder": "08:00",
        },
    )


# ---------------------------------------------------------------------------
# 好感度配置
# ---------------------------------------------------------------------------


class AffectionConfig(PluginConfigBase):
    """好感度档位：0=温柔有分寸，1=活泼热情，2=撒娇卖萌。"""

    __ui_label__: ClassVar[str] = "好感度"
    __ui_order__: ClassVar[int] = 5

    current_level: Literal[0, 1, 2] = Field(
        default=0,
        description="决定麦麦说话的语气。0=温柔有分寸，1=活泼热情，2=撒娇卖萌。",
        json_schema_extra={
            "hint": "0=温柔 | 1=活泼 | 2=撒娇。",
            "i18n": _schema_i18n(
                label_en="Affection Level",
                label_ja="好感度レベル",
                hint_en="0=gentle | 1=lively | 2=cute. Controls her tone.",
                hint_ja="0=優しい | 1=活発 | 2=甘えん坊。話し方のトーンを制御します。",
            ),
            "label": "好感度档位",
            "order": 0,
        },
    )

    @field_validator("current_level", mode="before")
    @classmethod
    def _normalize_level(cls, value: Any) -> int:
        return _normalize_int_in_range(value, 0, 0, 2)


# ---------------------------------------------------------------------------
# 恋人电脑（cateye 联动）
# ---------------------------------------------------------------------------


class CateyeConfig(PluginConfigBase):
    """联动「cateye 统一连接插件」（cateye.connect-hub）：想念/早晚安触发时
    看一眼恋人电脑，让麦麦知道 TA 在干什么。需要 cateye 客户端在用户电脑上在线。"""

    __ui_label__: ClassVar[str] = "恋人电脑（cateye）"
    __ui_order__: ClassVar[int] = 6

    enabled: bool = Field(
        default=False,
        description="开启后，想念回复与早安/晚安触发时会通过 cateye 插件查看恋人电脑："
                    "已连接则截图并用视觉模型转成一句话描述拼进提示词；未连接则告诉 LLM"
                    "「恋人的电脑没开」。需要同时安装 cateye.connect-hub 插件且客户端在线。",
        json_schema_extra={
            "hint": "默认关闭。开启前请确认已安装「cateye 统一连接插件」并在用户电脑上跑起客户端。",
            "i18n": _schema_i18n(
                label_en="Cateye Integration",
                label_ja="cateye 連携",
                hint_en="On miss/morning/night triggers, peek at the lover's PC via the cateye hub plugin.",
                hint_ja="発話時に cateye 経由で恋人の PC 状態を確認します。",
            ),
            "label": "启用恋人电脑联动",
            "order": 0,
        },
    )
    screenshot_blur: int = Field(
        default=0,
        description="截图模糊半径（0-64）。0=清晰截图；介意隐私可设 30 左右（视觉模型仍能看出大概在干什么）。",
        json_schema_extra={
            "hint": "0=不模糊看得最清；数值越大越模糊。",
            "i18n": _schema_i18n(
                label_en="Screenshot Blur",
                label_ja="スクリーンショットぼかし",
                hint_en="0 = clear. Larger values blur more (privacy).",
                hint_ja="0 = 鮮明。大きいほどぼかします。",
            ),
            "label": "截图模糊半径",
            "order": 1,
        },
    )
    vlm_task: str = Field(
        default="vlm",
        description="截图理解使用的视觉模型任务名（主程序 model_config.toml 的任务键，默认 vlm）。",
        json_schema_extra={
            "hint": "宿主不支持图片输入时截图描述自动降级为「电脑开着但没看清」。",
            "i18n": _schema_i18n(
                label_en="Vision Task Name",
                label_ja="視覚タスク名",
                hint_en="Model task used to interpret the screenshot (host model task key).",
                hint_ja="スクリーンショット解析に使うタスク名。",
            ),
            "label": "视觉模型任务名",
            "order": 2,
        },
    )
    timeout_seconds: float = Field(
        default=20.0,
        description="查看电脑（截图 + 视觉理解）的整体超时秒数，超时不阻塞巡检，本轮按不可用处理。",
        json_schema_extra={
            "hint": "秒。截图慢可调大；超时不会卡住主动消息，只会本轮看不到电脑状态。",
            "i18n": _schema_i18n(
                label_en="Peek Timeout (s)",
                label_ja="確認タイムアウト（秒）",
                hint_en="Overall timeout for screenshot + description.",
                hint_ja="スクリーンショット＋解析全体のタイムアウト。",
            ),
            "label": "查看超时（秒）",
            "order": 3,
        },
    )
    describe_prompt: str = Field(
        default=SCREEN_DESCRIBE_PROMPT_DEFAULT,
        description="屏幕截图的视觉理解提示词（随截图发给视觉模型，要求一句话描述用户在干什么）。",
        json_schema_extra={
            "hint": "想让麦麦关注别的细节可以改这里。",
            "i18n": _schema_i18n(
                label_en="Screen Describe Prompt",
                label_ja="画面説明プロンプト",
                hint_en="Prompt sent to the vision model along with the screenshot.",
                hint_ja="スクリーンショットと共に視覚モデルへ送るプロンプト。",
            ),
            "label": "屏幕理解提示词",
            "order": 4,
            "rows": 4,
        },
    )

    @field_validator("screenshot_blur", mode="before")
    @classmethod
    def _normalize_blur(cls, value: Any) -> int:
        return _normalize_int_in_range(value, 0, 0, 64)


# ---------------------------------------------------------------------------
# LLM 调用日志
# ---------------------------------------------------------------------------


class LLMLogConfig(PluginConfigBase):
    """插件发起的 LLM 调用日志：记录事件来源、时间与模型回复，供 /mai_llm_log 查看。"""

    __ui_label__: ClassVar[str] = "LLM 调用日志"
    __ui_order__: ClassVar[int] = 7

    enabled: bool = Field(
        default=True,
        description="记录本插件发起的所有 LLM 请求的回复内容（含事件来源与时间），"
                    "用 /mai_llm_log 命令通过合并转发查看。不影响正常功能。",
        json_schema_extra={
            "hint": "想排查「麦麦为什么这么说/把关为什么驳回」就开着。",
            "i18n": _schema_i18n(
                label_en="LLM Call Log",
                label_ja="LLM 呼び出しログ",
                hint_en="Record responses of all plugin-initiated LLM calls; view via /mai_llm_log.",
                hint_ja="プラグイン発の LLM 応答を記録し、/mai_llm_log で確認します。",
            ),
            "label": "启用 LLM 调用日志",
            "order": 0,
        },
    )
    retention_days: int = Field(
        default=3,
        description="日志保留天数，过期自动清理。",
        json_schema_extra={
            "hint": "默认 3 天；1-30 之间。",
            "i18n": _schema_i18n(
                label_en="Retention (days)",
                label_ja="保持日数",
                hint_en="Older log files are deleted automatically.",
                hint_ja="古いログは自動削除されます。",
            ),
            "label": "日志保留天数",
            "order": 1,
        },
    )

    @field_validator("retention_days", mode="before")
    @classmethod
    def _normalize_retention(cls, value: Any) -> int:
        return _normalize_int_in_range(value, 3, 1, 30)


# ---------------------------------------------------------------------------
# 顶层配置聚合
# ---------------------------------------------------------------------------


class MaiLoverPluginSettings(PluginConfigBase):
    """麦麦恋人插件完整配置。包含开关、白名单、调度、概率、时间窗、好感度、
    恋人电脑联动、LLM 日志八大模块。"""

    plugin: PluginConfig = Field(default_factory=PluginConfig)
    whitelist: WhitelistConfig = Field(default_factory=WhitelistConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    probability: ProbabilityConfig = Field(default_factory=ProbabilityConfig)
    time_windows: TimeWindowsConfig = Field(default_factory=TimeWindowsConfig)
    affection: AffectionConfig = Field(default_factory=AffectionConfig)
    cateye: CateyeConfig = Field(default_factory=CateyeConfig)
    llm_log: LLMLogConfig = Field(default_factory=LLMLogConfig)


# ---------------------------------------------------------------------------
# 通用校验辅助函数
# ---------------------------------------------------------------------------


def _normalize_int_in_range(value: Any, default: int, low: int, high: int) -> int:
    """规范化整数并限制在 [low, high] 范围内。"""
    if isinstance(value, str):
        try:
            value = int(value.strip())
        except (ValueError, TypeError):
            return default
    if isinstance(value, float):
        value = int(value)
    if isinstance(value, int):
        return max(low, min(high, value))
    return default


def _normalize_float_in_range(
    value: Any, default: float, low: float, high: float
) -> float:
    """规范化浮点数并限制在 [low, high] 范围内（兼容整数字符串输入）。"""
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except (ValueError, TypeError):
            return default
    if isinstance(value, (int, float)):
        return max(low, min(high, float(value)))
    return default
