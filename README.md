# 找话题的麦麦 (mai-topic-seeker)

群聊冷场时，让麦麦主动起个话头。

插件**不生成任何文本**、**不调用 LLM**、**不直接发消息** —— 它只判断「现在该不该开口」，然后把这件事作为一条意图交给 Maisaka Planner，由麦麦基于自己的人设、记忆和上下文决定要不要说、说什么、怎么说。

所以主动发言和被动回复用的是**同一套人格**，不会出现「主动说话时像另一个人」的情况。

## 特性

- 🎭 **人格统一**：走 Maisaka 管线，人设 / 记忆 / 表达方式全部复用
- 💤 **冷场判定**：按「距最后一条真人消息」的时长触发，麦麦自己发的不算
- 🙊 **不说独角戏**：会话里最后一条消息是麦麦自己发的就不开口
- 🎲 **概率 + 抖动**：检查间隔随机浮动，命中后还要再过一次概率
- 🕐 **时间段控制**：只在指定时段内主动，支持跨夜
- 🚫 **最小间隔**：两次主动之间强制冷却，避免刷屏
- 🔁 **目标流持续重试**：宿主重启后内存缓存为空也能恢复（见下）
- 🎯 **白名单**：精确控制哪些群 / 私聊生效
- 🖐 **手动触发**：`/topic` 立即来一次，`/topic status` 看运行状态

## 安装

把整个目录放进 MaiBot 的 `plugins/` 目录，重启（或等热重载）即可：

```bash
cd <MaiBot>/plugins
git clone https://github.com/WhiteElephant-abc/mai-topic-seeker.git
```

插件不需要打包，也不需要额外依赖。

## 配置

编辑插件目录下的 `config.toml`，至少填一个白名单：

```toml
[target]
allowed_groups = ["123456789"]   # 群号
allowed_friends = []             # 私聊 QQ 号
```

主要配置项：

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `scheduler.start_time` / `end_time` | `08:00` / `23:30` | 生效时段；`end` 小于 `start` 表示跨夜 |
| `scheduler.check_interval` | `25` | 检查间隔（分钟） |
| `scheduler.jitter` | `5` | 间隔抖动，实际间隔在 `25±5` 分钟内随机 |
| `scheduler.idle_minutes` | `45` | **冷场阈值**：距最后一条真人消息超过该时长才算冷场 |
| `scheduler.probability` | `0.6` | 冷场成立后，本次真正触发的概率 |
| `scheduler.min_interval_between_chats` | `90` | 两次主动之间的最小间隔（分钟） |
| `intent.text` | 见下 | 注入 Planner 的意图文本 |

`intent.text` 支持占位符 `{nickname}` `{idle_minutes}` `{time}` `{date}`，例如：

```toml
[intent]
text = "群里已经安静了 {idle_minutes} 分钟，现在没有需要回复的新消息。请你主动发一条新消息起个话头，不要引用或回复历史消息。"
```

写完保存即生效，宿主会热重载插件配置。

## 命令

两个命令都声明了 `permission="operator"`，**只有被授权的用户能执行**。其他人发送时会被宿主直接拦下并回复「你没有权限使用此命令。」，插件根本不会收到调用。

| 命令 | 说明 |
|---|---|
| `/topic` | 立刻让麦麦尝试主动起个话题（忽略时段、概率和最小间隔） |
| `/topic status` | 查看跟踪的目标、各自的冷场时长和已触发次数 |

授权写在**全局** `bot_config.toml` 里（不是插件自己的 `config.toml`）：

```toml
[plugin]
# 全局操作员，可以执行所有声明了 operator 权限的命令
permission = ["qq:123456789"]
```

只想放开这一个插件的话，用按命令细分的写法：

```toml
[plugin.command_permissions]
"whiteelephant.mai-topic-seeker.topic_seeker_trigger" = { allow_users = ["qq:123456789"] }
"whiteelephant.mai-topic-seeker.topic_seeker_status" = { allow_users = ["qq:123456789"] }
```

命令 ID 的格式是 `<插件 ID>.<命令名>`，`allow_users` 用 `平台:用户ID`，也可以用 `allow_chats = ["<聊天流 ID>"]` 按会话放行。另外从主程序的本地终端执行时不受此限制。

## 工作原理

```
调度 tick → 同步目标会话 → 时段检查 → 最小间隔检查
    → 最后一条消息是不是自己发的？
         是 → 本轮结束（等群友开口，不自言自语）
         否 ↓
    → 冷场检查（距最后一条真人消息） → 概率判定
        ↓ 全部通过
maisaka.proactive.trigger(stream_id, intent)
        ↓
Planner 收到意图 → 基于人设 / 记忆 / 上下文 / 工具
        ↓
自行决定是否开口、说什么、如何表达
```

触发之后**不代表一定会有消息发出** —— 麦麦可能正常接话，也可能只回一个「嗯」，或者判断此刻没必要说话。这是设计使然：插件只负责「提醒」，表达权在麦麦。

### 为什么不会自言自语

判定里有一条硬规则：**会话里最后一条消息必须来自群友**，否则本轮直接结束。

这条规则堵住的是最容易翻车的场景 —— 麦麦起了个话头，没人接，那条消息就成了会话里的最后一条。没有这条规则的话，冷场判定依然成立（最后一条*真人*消息还是很久以前那条），于是它过一会儿又找一个话题，一天能说上十来次，一次比一次尴尬。

所以实际行为是：

| 情况 | 行为 |
|---|---|
| 群友说了话，之后没人接 | 冷场够久 → 麦麦主动 ✓ |
| 麦麦说完，没人接 | **保持沉默**，直到群友再次开口 |
| 麦麦说完，群友接了，之后又冷场 | 正常触发 ✓ |

代价是：如果群真的死了，插件会彻底安静下来 —— 这是想要的结果。

### 目标会话是怎么解析的

这是同类插件最容易踩的坑：宿主的 `chat_manager.sessions` 是一个**内存缓存**，容器重启后是空的，而 `chat.get_stream_by_group_id` **只读这个内存**，于是插件在 `on_load` 里查一次就会一无所获 —— 而且如果只在加载时查一次，就再也恢复不了。

本插件按三级解析，并且**每次调度都重试**：

1. **宿主内存里的活跃流** —— 最准，但重启后拿不到；
2. **`chat_sessions` 表里的历史会话** —— 重启后的主路径。表里直接存着真实 `session_id`，读出来就能用，不需要自己按 `platform + account_id + group_id` 拼算，因此**不受 `bot_config.qq_account` 为空的影响**（那个字段只是适配器没上报身份时的备用值，实际部署中常常是空的）；
3. **`chat.open_session` 新建** —— 默认关闭（`allow_create_session = false`）。冷场本来就需要历史消息才有意义，而且新建会在数据库里留下垃圾记录。

`bot` 的账号 ID 优先取适配器实际上报的活跃账号（`bot_platform_accounts` 表），拿不到才回退到 `bot_config.qq_account`。

## 与 attention-seeker-mai 的关系

本插件受 [attention-seeker-mai](https://github.com/clfr41/attention-seeker-mai) 的启发而写，架构思路一致（都是把意图交给 Maisaka），主要区别：

- 目标流解析：三级回落 + 每轮重试（对方只在 `on_load` 解析一次，重启后永久失效）
- 冷场判定显式化：`idle_minutes` 是独立配置项，且排除麦麦自己的消息
- 配置热更新时会重新同步白名单
- 命令带状态查询，日志覆盖每个决策分支

## License

GPL-3.0-only，见 [LICENSE](LICENSE)。
