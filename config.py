"""找话题的麦麦 —— 插件配置模型。

字段说明会作为 Schema 出现在 WebUI 的插件配置页里。
"""

from __future__ import annotations

from typing import List

from maibot_sdk import Field, PluginConfigBase


class PluginSection(PluginConfigBase):
    """插件基础设置。"""

    enabled: bool = Field(default=True, description="是否启用插件")
    config_version: str = Field(default="1.0.0", description="配置文件版本号（内部使用，勿改）")
    version: str = Field(default="1.0.0", description="插件版本（内部使用，勿改）")


class SchedulerSection(PluginConfigBase):
    """调度设置 —— 控制什么时候、多久检查一次。"""

    enabled: bool = Field(default=True, description="是否开启主动找话题")
    start_time: str = Field(default="08:00", description="每日开始时间 HH:MM")
    end_time: str = Field(default="23:30", description="每日结束时间 HH:MM；小于开始时间表示跨夜")
    check_interval: int = Field(default=25, description="检查间隔（分钟）")
    jitter: int = Field(default=5, description="检查间隔的随机抖动（分钟），实际间隔在 ±该值内浮动")
    probability: float = Field(default=0.6, description="每次检查命中后的触发概率，0~1")
    idle_minutes: int = Field(
        default=45,
        description="冷场阈值（分钟）：距最后一条真人消息超过这个时长才算冷场",
    )
    min_interval_between_chats: int = Field(default=90, description="两次主动之间的最小间隔（分钟）")
    lookback_hours: int = Field(default=72, description="回看多少小时内的消息来判断是否冷场")


class TargetSection(PluginConfigBase):
    """目标白名单 —— 空列表表示该类目标完全不生效。"""

    platform: str = Field(default="qq", description="目标平台标识")
    allowed_groups: List[str] = Field(default_factory=list, description="允许主动找话题的群号")
    allowed_friends: List[str] = Field(default_factory=list, description="允许主动找话题的 QQ 号（私聊）")
    allow_create_session: bool = Field(
        default=False,
        description=(
            "数据库里查不到会话记录时，是否允许新建。默认关闭："
            "冷场本来就需要历史消息才有意义，而且新建会在数据库里留下垃圾记录"
        ),
    )


class IntentSection(PluginConfigBase):
    """注入给 Planner 的意图。"""

    text: str = Field(
        default="群里安静了一会儿了。你可以主动起个话头，或者接着之前没聊完的话题往下说。",
        description="意图文本，支持 {nickname} {idle_minutes} {time} {date} 占位符",
    )
    reason: str = Field(default="topic_seeker", description="触发原因标识，会带进 Planner 上下文")


class TopicSeekerConfig(PluginConfigBase):
    """插件完整配置。"""

    plugin: PluginSection = Field(default_factory=PluginSection)
    scheduler: SchedulerSection = Field(default_factory=SchedulerSection)
    target: TargetSection = Field(default_factory=TargetSection)
    intent: IntentSection = Field(default_factory=IntentSection)
