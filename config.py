"""找话题的麦麦 —— 插件配置模型。

WebUI 的配置页从这份模型生成：配置节的标题与说明来自 ``__ui_label__`` 和类
docstring，字段的标签、说明、排序来自 ``json_schema_extra`` 里的
``label`` / ``hint`` / ``order``。不加这些元数据时，界面会退化成直接显示
英文字段名，所以每个字段都补齐了。
"""

from __future__ import annotations

from typing import ClassVar, List

from maibot_sdk import Field, PluginConfigBase


class PluginSection(PluginConfigBase):
    """插件自身的开关。正常使用只需要确认「启用插件」是开的。"""

    __ui_label__: ClassVar[str] = "插件设置"
    __ui_order__: ClassVar[int] = 0

    enabled: bool = Field(
        default=True,
        description="是否启用插件。",
        json_schema_extra={
            "label": "启用插件",
            "hint": "关闭后插件完全停止，不会再有调度，也不会触发任何主动行为。",
            "order": 0,
        },
    )
    config_version: str = Field(
        default="1.0.0",
        description="当前配置结构版本。",
        json_schema_extra={
            "label": "配置版本",
            "hint": "供宿主做配置迁移使用，由插件维护，不要修改。",
            "order": 98,
            "disabled": True,
            "hidden": True,
        },
    )
    version: str = Field(
        default="1.0.0",
        description="插件版本。",
        json_schema_extra={
            "label": "插件版本",
            "hint": "由插件维护，不要修改。",
            "order": 99,
            "disabled": True,
            "hidden": True,
        },
    )


class SchedulerSection(PluginConfigBase):
    """决定什么时候检查、多久检查一次，以及什么情况才算得上「冷场」。"""

    __ui_label__: ClassVar[str] = "调度"
    __ui_order__: ClassVar[int] = 1

    enabled: bool = Field(
        default=True,
        description="是否开启主动找话题。",
        json_schema_extra={
            "label": "启用主动找话题",
            "hint": "关闭后调度器仍在运行，但不会触发；手动执行 /topic 依然有效。",
            "order": 0,
        },
    )
    start_time: str = Field(
        default="08:00",
        description="每日开始时间。",
        json_schema_extra={
            "label": "开始时间",
            "hint": "24 小时制 HH:MM。凌晨不想被打扰就把开始时间往后调。",
            "order": 1,
            "placeholder": "08:00",
        },
    )
    end_time: str = Field(
        default="23:30",
        description="每日结束时间。",
        json_schema_extra={
            "label": "结束时间",
            "hint": "结束时间小于开始时间表示跨夜，例如 20:00 — 02:00。",
            "order": 2,
            "placeholder": "23:30",
        },
    )
    check_interval: int = Field(
        default=25,
        description="检查间隔（分钟）。",
        json_schema_extra={
            "label": "检查间隔",
            "hint": "每隔多久看一次群里是不是冷场了。实际间隔会叠加下面的抖动值。",
            "order": 3,
            "min": 1,
        },
    )
    jitter: int = Field(
        default=5,
        description="检查间隔抖动（分钟）。",
        json_schema_extra={
            "label": "间隔抖动",
            "hint": "实际间隔在「检查间隔 ± 该值」之间随机，避免每天在固定时刻开口。填 0 表示不抖动。",
            "order": 4,
            "min": 0,
        },
    )
    idle_minutes: int = Field(
        default=45,
        description="冷场阈值（分钟）。",
        json_schema_extra={
            "label": "冷场阈值",
            "hint": (
                "距最后一条群友消息超过这个时长才算冷场。"
                "麦麦自己发的消息不计入，所以它说完话没人接的时候不会继续找话题。"
            ),
            "order": 5,
            "min": 1,
        },
    )
    probability: float = Field(
        default=0.6,
        description="触发概率。",
        json_schema_extra={
            "label": "触发概率",
            "hint": "冷场成立后，每次检查有多大概率真的把话题交给麦麦。0.6 表示大约六成的检查会触发。",
            "order": 6,
            "min": 0,
            "max": 1,
            "step": 0.05,
        },
    )
    min_interval_between_chats: int = Field(
        default=90,
        description="两次主动之间的最小间隔（分钟）。",
        json_schema_extra={
            "label": "最小间隔",
            "hint": "强制冷却时间。即使群里再次冷场，也不会在这个时长的冷却期内开口。",
            "order": 7,
            "min": 0,
        },
    )
    lookback_hours: int = Field(
        default=72,
        description="消息回看窗口（小时）。",
        json_schema_extra={
            "label": "消息回看窗口",
            "hint": "只在这个时间窗口内查找历史消息；窗口内一条消息都没有时，冷场计时从插件加载那一刻算起。",
            "order": 8,
            "min": 1,
        },
    )


class TargetSection(PluginConfigBase):
    """要主动找话题的对象。两个白名单都留空的话，插件不会在任何地方生效。"""

    __ui_label__: ClassVar[str] = "目标白名单"
    __ui_order__: ClassVar[int] = 2

    platform: str = Field(
        default="qq",
        description="目标平台标识。",
        json_schema_extra={
            "label": "平台",
            "hint": "通常保持默认的 qq 即可。",
            "order": 0,
        },
    )
    allowed_groups: List[str] = Field(
        default_factory=list,
        description="允许主动找话题的群号。",
        json_schema_extra={
            "label": "群聊白名单",
            "hint": "填群号，可以填多个，例如 [\"123456789\"]。留空表示不在任何群里主动。",
            "order": 1,
        },
    )
    allowed_friends: List[str] = Field(
        default_factory=list,
        description="允许主动找话题的 QQ 号（私聊）。",
        json_schema_extra={
            "label": "私聊白名单",
            "hint": "填对方的 QQ 号，可以填多个。留空表示不在任何私聊里主动。",
            "order": 2,
        },
    )
    allow_create_session: bool = Field(
        default=False,
        description="是否允许新建会话。",
        json_schema_extra={
            "label": "允许新建会话",
            "hint": (
                "默认关闭。数据库里查不到会话记录时是否允许新建 —— "
                "冷场判定本来就需要历史消息才有意义，而且新建会在数据库里留下多余记录，一般不用打开。"
            ),
            "order": 3,
        },
    )


class IntentSection(PluginConfigBase):
    """交给麦麦的意图。它只是「提醒麦麦该说点什么」，具体说什么由麦麦自己决定。"""

    __ui_label__: ClassVar[str] = "话题意图"
    __ui_order__: ClassVar[int] = 3

    text: str = Field(
        default="群里安静了一会儿了。你可以主动起个话头，或者接着之前没聊完的话题往下说。",
        description="注入 Planner 的意图文本。",
        json_schema_extra={
            "label": "意图文本",
            "hint": "支持占位符 {nickname} {idle_minutes} {time} {date}。写清楚「为什么要说话」即可，措辞由麦麦自己把握。",
            "order": 0,
            "rows": 3,
        },
    )
    reason: str = Field(
        default="topic_seeker",
        description="触发原因标识。",
        json_schema_extra={
            "label": "原因标识",
            "hint": "用于在日志和 Planner 上下文里标记这次主动的来源，方便回溯。",
            "order": 1,
        },
    )


class TopicSeekerConfig(PluginConfigBase):
    """找话题的麦麦 —— 群聊冷场时把「起个话头」的意图交给 Maisaka。"""

    plugin: PluginSection = Field(default_factory=PluginSection)
    scheduler: SchedulerSection = Field(default_factory=SchedulerSection)
    target: TargetSection = Field(default_factory=TargetSection)
    intent: IntentSection = Field(default_factory=IntentSection)
