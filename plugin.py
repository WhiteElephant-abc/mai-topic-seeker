"""找话题的麦麦 —— 群聊冷场时把「起个话头」交给 Maisaka Planner。

插件只做两件事：

1. 判断什么时候该由麦麦主动开口；
2. 把「该起个话头了」作为意图交给 Maisaka，由它基于人格、记忆和上下文
   自行决定要不要说、说什么、怎么说。

它不生成文本、不调用 LLM、不直接发消息，所以主动发言和被动回复用的是同一套
人格与记忆。

目标会话怎么解析（同类插件最容易踩的坑）：宿主重启后 ``chat_manager.sessions``
这个内存缓存是空的，只读内存的 ``chat.get_stream_by_group_id`` 必然查不到东西。
所以这里按三级解析，且每次调度都补一次，而不是只在 ``on_load`` 里解析一次：

1. 宿主内存里的活跃流 —— 最准，但重启后拿不到；
2. ``chat_sessions`` 表里的历史会话 —— 重启后的主路径，**表里直接存着真实
   session_id**，不需要自己算，也就不依赖 account_id；
3. ``chat.open_session`` 新建 —— 默认关闭，因为它会在数据库里留下垃圾记录，
   而「冷场」本来就需要历史消息才有意义。
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from datetime import datetime, time as datetime_time
from typing import Any, Dict, Optional

from maibot_sdk import ON_BOT_CONFIG_RELOAD, Command, MaiBotPlugin

from .config import TopicSeekerConfig

_DEFAULT_INTENT = "群里安静了一会儿了。你可以主动起个话头，或者接着之前没聊完的话题往下说。"


def _parse_hhmm(value: str) -> datetime_time:
    """解析 ``HH:MM`` 形式的时刻。"""

    hour_text, _, minute_text = str(value or "").strip().partition(":")
    hour = int(hour_text)
    minute = int(minute_text or "0")
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"非法时刻: {value!r}")
    return datetime_time(hour, minute)


def _kind_label(is_group: bool) -> str:
    """日志里用的目标类型名称。"""

    return "群聊" if is_group else "私聊"


@dataclass
class ChatPulse:
    """一个会话最近消息的观察结果。"""

    last_is_self: bool = False
    """最后一条消息是不是麦麦自己发的。"""

    last_human_at: float = 0.0
    """最后一条真人消息的时间戳，没有则 0。"""


@dataclass
class TargetState:
    """单个目标会话的运行时状态。"""

    target_id: str
    is_group: bool
    stream_id: str = ""
    last_proactive_at: float = 0.0
    trigger_count: int = 0
    create_attempted: bool = False

    @property
    def kind(self) -> str:
        return _kind_label(self.is_group)


class TopicSeekerPlugin(MaiBotPlugin):
    """冷场时通过 Maisaka 主动找话题的插件。"""

    config_model = TopicSeekerConfig

    # bot.nickname 与适配器账号来自全局配置，不订阅就不会收到它们的更新通知
    config_reload_subscriptions = {ON_BOT_CONFIG_RELOAD}

    def __init__(self) -> None:
        super().__init__()
        self._targets: Dict[str, TargetState] = {}
        self._scheduler_task: Optional[asyncio.Task] = None
        self._bot_account = ""
        self._nickname = "麦麦"
        self._loaded_at = 0.0
        self._platform = ""

    # ========== 生命周期 ==========

    async def on_load(self) -> None:
        self._loaded_at = time.time()
        self._nickname = await self._get_global_str("bot.nickname", "麦麦")
        self._bot_account = await self._resolve_bot_account()

        await self._sync_targets()
        self._start_scheduler()
        self.ctx.logger.info(
            "已加载：跟踪 %s 个目标，检查间隔 %s 分钟，冷场阈值 %s 分钟，bot 账号 %s",
            len(self._targets),
            self.config.scheduler.check_interval,
            self.config.scheduler.idle_minutes,
            self._bot_account or "未确定",
        )

    async def on_unload(self) -> None:
        if self._scheduler_task and not self._scheduler_task.done():
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
        self._scheduler_task = None
        self.ctx.logger.info("已卸载")

    async def on_config_update(self, scope: str, config_data: dict[str, object], version: str) -> None:
        del config_data, version

        if scope == ON_BOT_CONFIG_RELOAD:
            # 昵称与 bot 账号参与意图文本渲染和会话解析，但它们在全局配置里，
            # 不属于插件自己的 config.toml，只有订阅了才会收到这次回调。
            self._nickname = await self._get_global_str("bot.nickname", "麦麦")
            self._bot_account = await self._resolve_bot_account()
            self.ctx.logger.info(
                "全局配置已更新：昵称=%s，bot 账号=%s", self._nickname, self._bot_account or "未确定"
            )
            return

        if scope and scope != "self":
            return

        self.ctx.logger.info("插件配置已更新，重新同步目标会话")
        await self._sync_targets()

    # ========== 配置与账号 ==========

    async def _get_global_str(self, key: str, default: str) -> str:
        try:
            value = await self.ctx.config.get(key, default)
        except Exception as exc:  # noqa: BLE001 - 读不到配置不该让插件起不来
            self.ctx.logger.warning(f"读取全局配置 {key} 失败: {exc}")
            return default
        if isinstance(value, dict):
            value = value.get("value", default)
        return str(value or default)

    async def _resolve_bot_account(self) -> str:
        """确定 bot 在当前平台的账号 ID。

        优先取适配器实际上报的活跃账号（``bot_platform_accounts`` 表），
        ``bot_config`` 里的 ``qq_account`` 只是「适配器没上报身份时的备用值」，
        实际部署中经常是空的。
        """

        try:
            rows = await self.ctx.db.query(
                model_name="BotPlatformAccount",
                filters={"platform": self.config.target.platform, "disabled": False},
                order_by=["-last_seen_at"],
                limit=1,
            )
            if isinstance(rows, list) and rows and isinstance(rows[0], dict):
                account_id = str(rows[0].get("account_id") or "").strip()
                if account_id:
                    return account_id
        except Exception as exc:  # noqa: BLE001 - 拿不到就走备用配置
            self.ctx.logger.warning(f"查询适配器上报的 bot 账号失败: {exc}")

        fallback = await self._get_global_str("bot.qq_account", "")
        if fallback:
            self.ctx.logger.info(f"未查到适配器上报的账号，改用 bot_config 里的备用账号 {fallback}")
        return fallback

    # ========== 目标会话解析 ==========

    async def _sync_targets(self) -> None:
        """按配置增删目标，并补齐尚未解析出 stream_id 的条目。

        已解析成功的条目状态会被保留 —— 尤其是 ``last_proactive_at``，
        否则每轮补解析都会把「两次主动之间的最小间隔」重置掉。
        """

        # 平台换了，之前按旧平台算出来的 stream_id 全部作废
        platform = str(self.config.target.platform or "").strip()
        if platform != self._platform:
            if self._platform:
                self.ctx.logger.info(f"平台由 {self._platform} 改为 {platform}，重新解析全部目标")
            self._platform = platform
            for state in self._targets.values():
                state.stream_id = ""

        desired: Dict[str, bool] = {}
        for group_id in self.config.target.allowed_groups:
            normalized = str(group_id or "").strip()
            if normalized:
                desired[normalized] = True
        for user_id in self.config.target.allowed_friends:
            normalized = str(user_id or "").strip()
            if normalized:
                desired[normalized] = False

        for target_id in list(self._targets):
            if target_id not in desired:
                self.ctx.logger.info(f"目标 {target_id} 已从白名单移除，停止跟踪")
                del self._targets[target_id]

        for target_id, is_group in desired.items():
            state = self._targets.get(target_id)
            if state is None:
                state = TargetState(target_id=target_id, is_group=is_group)
                self._targets[target_id] = state
            elif state.is_group != is_group:
                # 同一个号从群聊改成私聊（或反之），旧 stream_id 不再适用
                state.is_group = is_group
                state.stream_id = ""
            if not state.stream_id:
                await self._resolve_target(state)

    async def _resolve_target(self, state: TargetState) -> bool:
        """解析目标会话的 stream_id；失败时留待下一轮调度重试。"""

        stream_id = await self._lookup_active_stream(state.target_id, state.is_group)
        if not stream_id:
            stream_id = await self._lookup_persisted_stream(state.target_id, state.is_group)
        if not stream_id and self.config.target.allow_create_session and not state.create_attempted:
            state.create_attempted = True
            stream_id = await self._create_stream(state.target_id, state.is_group)

        if not stream_id:
            self.ctx.logger.warning(
                f"{state.kind} {state.target_id} 暂时解析不到聊天流，将在下个调度周期重试"
                "（宿主刚重启时内存缓存为空属正常，等群里有人说话即可从数据库回落）"
            )
            return False

        state.stream_id = stream_id
        self.ctx.logger.info(f"已解析{state.kind} {state.target_id} → {stream_id}")
        return True

    async def _lookup_active_stream(self, target_id: str, is_group: bool) -> str:
        """第一级：宿主内存里的活跃流。只读内存，重启后为空。"""

        try:
            if is_group:
                stream = await self.ctx.chat.get_stream_by_group_id(
                    target_id, platform=self.config.target.platform
                )
            else:
                stream = await self.ctx.chat.get_stream_by_user_id(
                    target_id, platform=self.config.target.platform
                )
        except Exception as exc:  # noqa: BLE001 - 失败就走下一级
            self.ctx.logger.debug(f"查询活跃聊天流失败 ({target_id}): {exc}")
            return ""

        if not isinstance(stream, dict):
            return ""
        return str(stream.get("session_id") or stream.get("stream_id") or "")

    async def _lookup_persisted_stream(self, target_id: str, is_group: bool) -> str:
        """第二级：数据库里的历史会话，这是宿主重启后的主路径。

        ``chat_sessions`` 表里直接存着真实 ``session_id``，读出来就能用，
        不需要自己按 platform + account_id + group_id 拼算，因此也不受
        ``bot_config.qq_account`` 为空的影响。

        同一个目标可能有多条记录（换过 bot 账号就会多一条），按最后活跃时间
        取最新的那条。
        """

        filters: Dict[str, Any] = {"platform": self.config.target.platform}
        filters["group_id" if is_group else "user_id"] = target_id
        try:
            rows = await self.ctx.db.query(
                model_name="ChatSession",
                filters=filters,
                order_by=["-last_active_timestamp"],
                limit=1,
            )
        except Exception as exc:  # noqa: BLE001 - 失败就走下一级
            self.ctx.logger.warning(f"查询历史会话失败 ({target_id}): {exc}")
            return ""

        if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
            return ""
        return str(rows[0].get("session_id") or "")

    async def _create_stream(self, target_id: str, is_group: bool) -> str:
        """第三级（默认关闭）：``chat.open_session`` 新建会话。

        ``account_id`` 必须与真实消息一致 —— session_id 由
        ``md5(platform + "account:{account_id}" + group_id)`` 算出，账号不对
        就会造出一个收发都不通的空会话，所以拿不到账号时宁可不建。
        """

        if not self._bot_account:
            self.ctx.logger.warning(
                f"{target_id} 在数据库里没有会话记录，且无法确定 bot 账号，跳过新建"
            )
            return ""

        try:
            if is_group:
                result = await self.ctx.chat.open_session(
                    platform=self.config.target.platform,
                    chat_type="group",
                    group_id=target_id,
                    account_id=self._bot_account,
                )
            else:
                result = await self.ctx.chat.open_session(
                    platform=self.config.target.platform,
                    chat_type="private",
                    user_id=target_id,
                    account_id=self._bot_account,
                )
        except Exception as exc:  # noqa: BLE001 - 失败就走重试
            self.ctx.logger.warning(f"open_session 调用失败 ({target_id}): {exc}")
            return ""

        # chat.open_session 不在 SDK 的解包表里，拿到的是宿主完整信封
        if not isinstance(result, dict) or not result.get("success"):
            reason = result.get("error") if isinstance(result, dict) else result
            self.ctx.logger.warning(f"open_session 未成功 ({target_id}): {reason}")
            return ""

        stream = result.get("stream")
        stream_id = str(
            result.get("session_id")
            or (stream.get("session_id") if isinstance(stream, dict) else "")
            or ""
        )
        if stream_id and result.get("created"):
            self.ctx.logger.warning(
                f"{_kind_label(is_group)} {target_id} 此前没有任何会话记录，已新建 —— "
                "请确认白名单里的号码是否正确"
            )
        return stream_id

    # ========== 调度 ==========

    def _start_scheduler(self) -> None:
        if self._scheduler_task and not self._scheduler_task.done():
            self._scheduler_task.cancel()
        self._scheduler_task = asyncio.create_task(self._schedule_loop())

    async def _schedule_loop(self) -> None:
        while True:
            try:
                interval = self._next_interval_seconds()
                self.ctx.logger.debug(f"[调度] 下次检查 {interval / 60:.0f} 分钟后")
                await asyncio.sleep(interval)
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 调度循环不能因单次异常退出
                self.ctx.logger.error(f"调度循环异常: {exc}", exc_info=True)
                await asyncio.sleep(60)

    def _next_interval_seconds(self) -> float:
        base = max(1, int(self.config.scheduler.check_interval))
        jitter = max(0, int(self.config.scheduler.jitter))
        minutes = base + (random.randint(-jitter, jitter) if jitter else 0)
        return max(60.0, minutes * 60.0)

    async def _tick(self) -> None:
        if not self._enabled():
            return

        # 每轮都补一次解析：宿主重启后内存流缓存为空，得等真实流量或数据库回落
        await self._sync_targets()

        if not self._targets:
            self.ctx.logger.warning("白名单里没有可用目标，检查 [target] 下的 allowed_groups / allowed_friends")
            return

        for state in list(self._targets.values()):
            try:
                await self._maybe_trigger(state)
            except Exception as exc:  # noqa: BLE001 - 单个目标失败不影响其他目标
                self.ctx.logger.error(f"[{state.target_id}] 检查失败: {exc}", exc_info=True)

    def _enabled(self) -> bool:
        return bool(self.config.plugin.enabled and self.config.scheduler.enabled)

    # ========== 触发判断 ==========

    async def _maybe_trigger(self, state: TargetState) -> None:
        if not state.stream_id:
            return

        if not self._in_time_range(datetime.now().time()):
            return

        min_gap = max(0, int(self.config.scheduler.min_interval_between_chats)) * 60
        now = time.time()
        if state.last_proactive_at and now - state.last_proactive_at < min_gap:
            self.ctx.logger.debug(f"[{state.target_id}] 距上次主动不足 {min_gap / 60:.0f} 分钟，跳过")
            return

        pulse = await self._read_chat_pulse(state.stream_id)
        if pulse.last_is_self:
            # 麦麦说完话没人接的时候，最后一条就是它自己发的。
            # 这时再主动就不是「找话题」而是对着空气自言自语了 —— 等群友开口再说。
            self.ctx.logger.debug(f"[{state.target_id}] 最后一条消息是麦麦自己发的，等群友开口")
            return

        idle_seconds = self._idle_seconds_from(pulse)
        required = max(1, int(self.config.scheduler.idle_minutes)) * 60
        if idle_seconds < required:
            self.ctx.logger.debug(
                f"[{state.target_id}] {idle_seconds / 60:.1f} 分钟前还有人说话，"
                f"未达到 {required / 60:.0f} 分钟的冷场阈值"
            )
            return

        if random.random() >= float(self.config.scheduler.probability):
            self.ctx.logger.info(
                f"[{state.target_id}] 已冷场 {idle_seconds / 60:.1f} 分钟，但本次概率未命中"
            )
            return

        await self._trigger(state, idle_seconds)

    def _in_time_range(self, current: datetime_time) -> bool:
        try:
            start = _parse_hhmm(self.config.scheduler.start_time)
            end = _parse_hhmm(self.config.scheduler.end_time)
        except (TypeError, ValueError) as exc:
            self.ctx.logger.warning(f"时间段配置无法解析，本轮不限制时间: {exc}")
            return True
        if start <= end:
            return start <= current <= end
        return current >= start or current <= end  # 跨夜

    def _idle_seconds_from(self, pulse: ChatPulse) -> float:
        """由观察结果算出「距最后一条真人消息」的秒数。

        会话里一条消息都查不到时（宿主刚重启、或这是个从没聊过的新会话），
        以插件加载时刻起算，避免刚启动就开麦。
        """

        baseline = max(pulse.last_human_at, self._loaded_at) if pulse.last_human_at else self._loaded_at
        return max(0.0, time.time() - baseline)

    async def _idle_seconds(self, stream_id: str) -> float:
        """自己查一次并算出冷场时长，供命令与手动触发使用。"""

        return self._idle_seconds_from(await self._read_chat_pulse(stream_id))

    @staticmethod
    def _sender_id(message: dict[str, Any]) -> str:
        """从消息字典里取发送者 ID。"""

        info = message.get("message_info")
        if not isinstance(info, dict):
            return ""
        user = info.get("user_info")
        if not isinstance(user, dict):
            return ""
        return str(user.get("user_id") or "")

    async def _read_chat_pulse(self, stream_id: str) -> ChatPulse:
        """看一眼会话最近的消息，得到「最后一条是不是自己发的」和「最后一条真人消息的时间」。

        前者的用途是拦住自言自语：麦麦说完话没人接的时候，最后一条就是它自己发的，
        这时候再主动就不是找话题了。按时间戳自己挑最新的一条，不依赖返回顺序。
        """

        window = max(1, int(self.config.scheduler.lookback_hours)) * 3600
        now = time.time()
        try:
            raw = await self.ctx.message.get_by_time_in_chat(
                chat_id=stream_id,
                start_time=str(now - window),
                end_time=str(now),
                limit=20,
                limit_mode="latest",
            )
        except Exception as exc:  # noqa: BLE001 - 查不到就当没有消息
            self.ctx.logger.warning(f"查询会话消息失败 ({stream_id}): {exc}")
            return ChatPulse()

        # SDK 会把宿主信封解包成 messages 列表，这里兼容未解包的情况
        messages = raw.get("messages") if isinstance(raw, dict) else raw
        if not isinstance(messages, list):
            return ChatPulse()

        parsed: list[tuple[float, bool]] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            try:
                timestamp = float(message.get("timestamp") or 0.0)
            except (TypeError, ValueError):
                continue
            is_self = bool(self._bot_account) and self._sender_id(message) == self._bot_account
            parsed.append((timestamp, is_self))

        if not parsed:
            return ChatPulse()

        _, latest_is_self = max(parsed, key=lambda item: item[0])
        pulse = ChatPulse(last_is_self=latest_is_self)
        # 麦麦自己说的话不算「有人在聊」，冷场时长只看真人消息
        pulse.last_human_at = max((timestamp for timestamp, is_self in parsed if not is_self), default=0.0)
        return pulse

    async def _trigger(self, state: TargetState, idle_seconds: float) -> bool:
        """把意图交给 Maisaka；是否真的开口由 Planner 决定。"""

        intent = self._render_intent(idle_seconds)
        try:
            await self.ctx.maisaka.proactive.trigger(
                stream_id=state.stream_id,
                intent=intent,
                reason=self.config.intent.reason,
                metadata={
                    "source": "mai-topic-seeker",
                    "target_id": state.target_id,
                    "idle_minutes": round(idle_seconds / 60, 1),
                },
            )
        except Exception as exc:  # noqa: BLE001 - 失败不更新计时，下轮还能重试
            self.ctx.logger.error(f"[{state.target_id}] 注入意图失败: {exc}", exc_info=True)
            return False

        state.last_proactive_at = time.time()
        state.trigger_count += 1
        self.ctx.logger.info(
            f"[{state.target_id}] 已把意图交给麦麦（冷场 {idle_seconds / 60:.1f} 分钟，"
            f"该目标累计 {state.trigger_count} 次）：{intent}"
        )
        return True

    def _render_intent(self, idle_seconds: float) -> str:
        template = str(self.config.intent.text or "").strip() or _DEFAULT_INTENT
        now = datetime.now()
        values = {
            "nickname": self._nickname,
            "idle_minutes": f"{idle_seconds / 60:.0f}",
            "time": now.strftime("%H:%M"),
            "date": now.strftime("%Y-%m-%d"),
        }
        try:
            return template.format(**values)
        except (KeyError, IndexError, ValueError):
            # 模板里写了未知占位符就原样使用，别让一次笔误让插件彻底失效
            return template

    # ========== 命令 ==========

    async def _reply(self, text: str, stream_id: str, *, success: bool = True):
        """把命令结果发到会话里，并把同样的文本回给宿主记日志。

        命令返回的三元组是 ``(是否成功, 返回文本, 是否拦截后续消息)`` ——
        返回文本宿主只用于日志，不会替插件发出去，所以得自己调 send。
        """

        if stream_id:
            await self.ctx.send.text(text, stream_id)
        return success, text, True

    def _state_by_stream(self, stream_id: str) -> Optional[TargetState]:
        normalized = str(stream_id or "").strip()
        if not normalized:
            return None
        for state in self._targets.values():
            if state.stream_id == normalized:
                return state
        return None

    @Command(
        "topic_seeker_status",
        description="查看找话题插件的运行状态",
        pattern=r"^/topic\s+status$",
        permission="operator",
    )
    async def cmd_status(self, stream_id: str = "", **kwargs: Any):
        del kwargs
        lines = [
            f"插件: {'启用' if self._enabled() else '停用'}",
            f"调度: 每 {self.config.scheduler.check_interval}±{self.config.scheduler.jitter} 分钟，"
            f"时间段 {self.config.scheduler.start_time}-{self.config.scheduler.end_time}",
            f"冷场阈值: {self.config.scheduler.idle_minutes} 分钟，"
            f"两次主动最小间隔: {self.config.scheduler.min_interval_between_chats} 分钟",
        ]
        if not self._targets:
            lines.append("目标: 无（检查 [target] 配置）")
        for state in self._targets.values():
            if state.stream_id:
                idle = await self._idle_seconds(state.stream_id)
                detail = f"冷场 {idle / 60:.0f} 分钟，已触发 {state.trigger_count} 次"
            else:
                detail = "尚未解析出聊天流"
            lines.append(f"{state.kind} {state.target_id}: {state.stream_id or '-'}，{detail}")
        if self._state_by_stream(stream_id) is None:
            lines.append("（当前会话不在白名单里）")
        return await self._reply("\n".join(lines), stream_id)

    @Command(
        "topic_seeker_trigger",
        description="立刻让麦麦尝试主动起个话题",
        pattern=r"^/topic(?:\s+trigger)?$",
        permission="operator",
    )
    async def cmd_trigger(self, stream_id: str = "", **kwargs: Any):
        del kwargs
        state = self._state_by_stream(stream_id)
        if state is None:
            return await self._reply("会话不在白名单", stream_id, success=False)
        if not state.stream_id:
            await self._resolve_target(state)
        if not state.stream_id:
            return await self._reply("聊天流未解析", stream_id, success=False)

        idle_seconds = await self._idle_seconds(state.stream_id)
        if await self._trigger(state, idle_seconds):
            return await self._reply("触发成功", stream_id)
        return await self._reply("触发失败", stream_id, success=False)


def create_plugin():
    """插件入口：Runner 会调用这个无参工厂函数。"""

    return TopicSeekerPlugin()
