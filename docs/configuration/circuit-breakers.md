# 熔断器配置

本文档详细介绍系统的熔断器配置，包括下载器熔断器和 IP 熔断器。

## 目录

- [下载器熔断器](#下载器熔断器)
- [IP 熔断器](#ip-熔断器)
- [配置示例](#配置示例)

---

## 下载器熔断器

### 工作原理

系统采用熔断器模式保护下载器，避免持续性故障影响服务。

**熔断器状态机**：

```
CLOSED（正常）
  ├─ 连续失败 5 次 → OPEN（熔断）
  │
OPEN（熔断）
  ├─ 拒绝所有请求，直接跳过该下载器
  ├─ 等待 30 分钟 → HALF_OPEN（半开）
  │
HALF_OPEN（半开）
  ├─ 允许 3 次测试请求
  ├─ 连续成功 2 次 → CLOSED（恢复）
  └─ 失败 → OPEN（重新熔断）
```

### 实际效果

```
场景：yt-dlp 遇到 YouTube 限流

时刻 10:00 - yt-dlp 连续失败 5 次
           → 熔断器开启

时刻 10:00-10:30 - 所有任务直接使用 TikHub
                 → 跳过 yt-dlp，节省时间

时刻 10:30 - 熔断器恢复，重新尝试 yt-dlp
```

### 配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `CIRCUIT_BREAKER_ENABLED` | `true` | 启用熔断器 |
| `CIRCUIT_BREAKER_THRESHOLD` | `5` | 连续失败阈值 |
| `CIRCUIT_BREAKER_TIMEOUT` | `1800` | 熔断器超时（秒，30分钟） |
| `CIRCUIT_BREAKER_HALF_OPEN_CALLS` | `3` | 半开状态最大调用次数 |

### 配置示例

#### 激进策略（适合频繁限流的环境）

```bash
CIRCUIT_BREAKER_ENABLED=true
CIRCUIT_BREAKER_THRESHOLD=3  # 3 次失败即熔断
CIRCUIT_BREAKER_TIMEOUT=900  # 15 分钟后恢复
```

#### 保守策略（适合偶尔限流的环境）

```bash
CIRCUIT_BREAKER_ENABLED=true
CIRCUIT_BREAKER_THRESHOLD=10  # 10 次失败才熔断
CIRCUIT_BREAKER_TIMEOUT=3600  # 1 小时后恢复
```

#### 禁用熔断器（不推荐）

```bash
CIRCUIT_BREAKER_ENABLED=false
```

---

## IP 熔断器

### 工作原理

系统采用被动探测型 IP 熔断机制，智能应对 YouTube 风控。

**状态机**：

```
NORMAL（正常）
  ↓ 音频任务 403
AUDIO_BANNED（音频熔断）
  ↓ 字幕任务也 403
FULLY_BANNED（全局熔断）
  ↓ 等待时间到期后探测
降级或恢复到 NORMAL
```

### 熔断级别

| 级别 | 说明 | 允许任务 | 探测策略 |
|------|------|----------|----------|
| `NORMAL` | 正常状态 | 所有任务 | - |
| `AUDIO_BANNED` | 音频熔断 | 仅字幕任务 | 利用字幕任务探测 |
| `FULLY_BANNED` | 全局熔断 | 无任务 | 等待时间到期后探测 |

### 被动探测机制

IP 熔断器采用**被动探测**策略：
- **不主动发起测试请求**，避免额外的风控风险
- **利用实际任务**进行探测恢复
- 智能决策：
  - `AUDIO_BANNED` 时：允许字幕任务执行，作为探测
  - `FULLY_BANNED` 时：等待时间到期后，允许下一个任务作为探测
  - 探测成功：降级或恢复到正常状态
  - 探测失败：延长熔断时间，避免频繁尝试

### 配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `IP_BAN_MIN_WAIT_BEFORE_RETRY` | `3600` | 最小等待时间（秒），触发熔断后必须等待这么久才允许重试 |
| `IP_BAN_MAX_RETRY_INTERVAL` | `1800` | 重试间隔（秒），失败后至少等待这么久才允许下次尝试 |

### 状态持久化与重启恢复

IP 熔断器每次状态变更（触发/升级/降级/恢复正常/启动时恢复）都会实时持久化到数据库：`ip_ban_status` 表保存当前状态快照（单行 upsert），`ip_ban_history` 表追加一条事件记录（`triggered`/`upgraded`/`downgraded`/`recovered`/`restored`）。持久化写入失败是 fail-open 的：只记日志，不影响熔断器本身的运行——熔断器本身不直接依赖数据库，保持纯内存、可独立单测。

这解决的问题是：熔断器状态原本完全在内存中，服务重启（例如 D3 自动部署每次 push 到 main 触发的容器重启）会丢失熔断状态，重启后服务会误判为 `NORMAL` 全速请求 YouTube，加重封禁风险。

```
场景：服务在 AUDIO_BANNED 期间重启

时刻 10:00 - 音频任务 403，触发 AUDIO_BANNED
           → 状态写入 ip_ban_status，历史写入 ip_ban_history

时刻 10:20 - D3 自动部署触发容器重启
           → 内存中的熔断器状态丢失

时刻 10:20 - Worker 启动，调用 _restore_ip_ban_state()
           → 读取 ip_ban_status，判定仍未过期（尚未到 11:00）
           → 熔断状态原样恢复到内存中的 IPBanCircuitBreaker 实例

时刻 10:20-11:00 - 继续保持 AUDIO_BANNED，仅允许字幕任务
                   → 避免误判为 NORMAL 全速请求 YouTube
```

是否恢复的判定使用与 `get_estimated_recovery_time` 完全一致的公式（`calculate_ban_recovery_time`）：取 `banned_at + IP_BAN_MIN_WAIT_BEFORE_RETRY` 与 `last_attempt_at + IP_BAN_MAX_RETRY_INTERVAL` 中较大的一个作为最早恢复时间；已过期则忽略，保持 `NORMAL`。

### 实际效果

```
场景：YouTube 检测到自动化下载

时刻 10:00 - 音频任务返回 403
           → 触发 AUDIO_BANNED

时刻 10:00-11:00 - 仅允许字幕任务
                   → 字幕任务成功执行
                   → 说明 IP 没有完全被封

时刻 11:00 - 音频任务再次 403
           → 升级到 FULLY_BANNED

时刻 11:00-12:00 - 等待 60 分钟
                   → 所有任务暂停

时刻 12:00 - 允许字幕任务探测
           → 字幕成功
           → 降级到 AUDIO_BANNED

时刻 12:30 - 字幕任务失败
           → 延长熔断时间
```

---

## 配置示例

### 完整配置示例

```bash
# ====== 下载器熔断器 ======
CIRCUIT_BREAKER_ENABLED=true
CIRCUIT_BREAKER_THRESHOLD=5
CIRCUIT_BREAKER_TIMEOUT=1800
CIRCUIT_BREAKER_HALF_OPEN_CALLS=3

# ====== IP 熔断器 ======
IP_BAN_MIN_WAIT_BEFORE_RETRY=3600
IP_BAN_MAX_RETRY_INTERVAL=1800

# ====== 间隔策略 ======
TRANSCRIPT_INTERVAL_MIN=20
TRANSCRIPT_INTERVAL_MAX=40
AUDIO_INTERVAL_MIN=60
AUDIO_INTERVAL_MAX=600
```

---

## 相关文档

- [配置总览](./overview.md)
- [下载器配置](./downloaders.md)
- [故障排查](../operations/troubleshooting.md)
