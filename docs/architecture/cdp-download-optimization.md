# CDP 分片下载风控优化思路

> 本文档记录 CDP 下载器分片下载策略的风控分析和优化方向，供后续迭代参考。

## 当前实现分析

### 现有策略

文件位置：`src/downloaders/cdp/audio_downloader.py`

```python
# 当前分片下载核心逻辑
- 动态分片：2-8MB 随机大小
- 并发控制：信号量限制最多 6 个并发
- 启动延迟：100-400ms 随机延迟
- 任务间隔：20ms 顺序创建任务
- 重试机制：3 次重试，指数退避
```

### 风控风险点

从服务端风控视角，当前实现存在以下可识别特征：

#### 1. 并发模式不符合播放器行为

| 特征 | 真实播放器 | 当前实现 |
|------|-----------|----------|
| 请求模式 | 顺序流式 | 并发下载 |
| 同时在途请求 | 1-2 个 | 最多 6 个 |
| 请求触发 | 缓冲区不足时 | 立即全部发起 |

**问题**：服务端看到同一时间多个 Range 请求并行，这是典型的下载工具特征。

#### 2. 请求时间间隔不真实

```
音频码率：128kbps
2MB 分片 ≈ 125 秒音频内容

真实播放器：播放 100+ 秒后才请求下一段
当前实现：100-400ms 后就请求下一段

差距：约 300 倍
```

#### 3. Range 请求可能乱序

并发执行导致请求可能乱序到达服务端：

```
期望顺序：bytes=0-5242879 → bytes=5242880-10485759 → ...
实际可能：bytes=0-5242879 → bytes=15728640-20971519 → bytes=5242880-...
```

真实播放器除 seek 操作外，几乎不会出现跳跃式请求。

#### 4. 缺少播放器初始化行为

真实播放器启动流程：
1. 请求小分片探测（几 KB）
2. 解析音频元数据
3. 根据网络状况调整缓冲策略
4. 开始正常缓冲

当前实现直接从 `bytes=0-XXXXXX` 开始大块下载。

#### 5. 缺少真实用户行为模式

| 行为 | 真实用户 | 当前实现 |
|------|---------|----------|
| 暂停 | 随机发生 | 无 |
| Seek | 偶尔跳跃 | 无 |
| 网络波动 | 请求间隔不规则 | 过于规整 |
| 放弃播放 | 可能中途停止 | 总是完整下载 |

#### 6. 连接复用模式

```
真实浏览器：
- HTTP/2 多路复用，同一 TCP 连接
- 同域名最多 6 个连接

当前实现：
- curl_cffi 可能每次创建新连接
- 短时间大量新连接建立
```

---

## 优化方案

### 方案一：渐进式预缓冲模式（推荐）

模拟播放器的**预缓冲行为**，这是真实播放器的正常操作。

#### 核心思路

```
真实播放器行为：
1. 开始播放前，快速预缓冲 10-30 秒内容
2. 播放开始后，保持缓冲区领先播放进度 30-60 秒
3. 网络好时，缓冲区会逐渐扩大
```

#### 分阶段下载

```python
async def _download_with_progressive_buffering(...):
    """
    渐进式预缓冲下载策略。

    阶段划分：
    1. 探测阶段：8KB 小请求获取元数据
    2. 预缓冲阶段：快速下载前 20%（2 并发，短间隔）
    3. 播放阶段：降速下载剩余 80%（1 并发，长间隔）
    """

    # 阶段 1：探测请求
    await self._send_probe_request(url, headers, 8192)
    await asyncio.sleep(random.uniform(0.5, 1.0))

    # 阶段 2：预缓冲（快速）
    await self._download_phase(
        ranges=prebuffer_ranges,  # 前 20%
        max_concurrent=2,
        delay_range=(0.3, 0.8),
    )

    # 阶段 3：播放阶段（慢速）
    await self._download_phase(
        ranges=playback_ranges,  # 剩余 80%
        max_concurrent=1,
        delay_range=(1.0, 3.0),
        add_pauses=True,
    )
```

#### 参数调整

| 参数 | 当前值 | 优化值 | 说明 |
|------|--------|--------|------|
| 最大并发 | 6 | 预缓冲 2 / 播放 1 | 降低并发特征 |
| 请求间隔 | 100-400ms | 预缓冲 300-800ms / 播放 1-3s | 更接近真实 |
| 任务启动间隔 | 20ms | 100-200ms | 避免请求堆积 |
| 随机暂停 | 无 | 5-10% 概率暂停 3-10s | 模拟用户行为 |
| 探测请求 | 无 | 8KB 初始请求 | 模拟播放器初始化 |

---

### 方案二：可配置的风险等级

提供多档配置，让运维根据实际情况选择：

```python
# 配置项
CDP_DOWNLOAD_MODE = "balanced"  # aggressive / balanced / stealth

DOWNLOAD_PROFILES = {
    "aggressive": {
        # 当前模式，快速但高风险
        "prebuffer_concurrent": 4,
        "playback_concurrent": 2,
        "prebuffer_delay": (0.1, 0.3),
        "playback_delay": (0.3, 0.8),
        "add_pauses": False,
        "probe_request": False,
    },
    "balanced": {
        # 推荐，速度与风控平衡
        "prebuffer_concurrent": 2,
        "playback_concurrent": 1,
        "prebuffer_delay": (0.3, 0.8),
        "playback_delay": (1.0, 3.0),
        "add_pauses": True,
        "pause_probability": 0.05,
        "probe_request": True,
    },
    "stealth": {
        # 最安全，接近真实播放速度
        "prebuffer_concurrent": 1,
        "playback_concurrent": 1,
        "prebuffer_delay": (1.0, 2.0),
        "playback_delay": (3.0, 8.0),
        "add_pauses": True,
        "pause_probability": 0.1,
        "probe_request": True,
    },
}
```

---

### 方案三：补充行为模拟

#### 3.1 探测请求

```python
async def _send_probe_request(
    self,
    url: str,
    headers: Dict[str, str],
    probe_size: int = 8192,
) -> None:
    """
    发送探测请求，模拟播放器初始化。

    真实播放器会先请求一小段数据来：
    - 验证 URL 有效性
    - 获取音频元数据（codec, duration 等）
    - 探测网络状况
    """
    probe_headers = headers.copy()
    probe_headers["Range"] = f"bytes=0-{probe_size - 1}"

    # 发送请求但不保存数据
    response = await self._make_request(url, probe_headers)
    # 丢弃响应内容，仅用于模拟行为
```

#### 3.2 随机暂停

```python
async def _maybe_pause(self, probability: float = 0.05) -> None:
    """
    随机暂停，模拟用户行为。

    真实用户可能：
    - 暂停播放去做其他事
    - 切换标签页导致播放暂停
    - 网络波动导致缓冲停滞
    """
    if random.random() < probability:
        pause_duration = random.uniform(3, 10)
        logger.debug(f"Simulating pause: {pause_duration:.1f}s")
        await asyncio.sleep(pause_duration)
```

#### 3.3 请求顺序保证

```python
async def _download_sequential_with_delay(
    self,
    ranges: List[Tuple[int, int, int]],
    delay_range: Tuple[float, float],
) -> None:
    """
    严格顺序下载，确保请求顺序与分片顺序一致。
    """
    for idx, start, end in ranges:
        await self._download_single_chunk(idx, start, end)

        # 根据分片大小计算"播放时间"
        chunk_size = end - start + 1
        play_duration = chunk_size / (128 * 1024 / 8)  # 128kbps

        # 实际延迟：播放时间的一定比例 + 随机因素
        delay = play_duration * random.uniform(0.1, 0.3)
        delay = max(delay, random.uniform(*delay_range))

        await asyncio.sleep(delay)
```

---

## 性能影响评估

以 10MB 音频文件为例：

| 模式 | 预计下载时间 | 风控风险 | 适用场景 |
|------|-------------|----------|----------|
| aggressive | 5-10 秒 | 高 | IP 充足，追求速度 |
| balanced | 30-50 秒 | 低 | 日常使用推荐 |
| stealth | 2-3 分钟 | 极低 | IP 紧张，避免封禁 |

### balanced 模式时间分解

```
探测阶段:     ~1 秒
预缓冲 (2MB): ~3-5 秒 (2 并发)
播放阶段 (8MB): ~20-40 秒 (1 并发)
随机暂停:     ~5 秒 (平均)
---
总计:         ~30-50 秒
```

---

## 实施建议

### 优先级排序

1. **P0 - 降低并发数**：从 6 降到 2-3，最简单有效
2. **P1 - 增加请求间隔**：从毫秒级提升到秒级
3. **P2 - 添加探测请求**：模拟播放器初始化
4. **P3 - 分阶段下载**：预缓冲 + 播放两阶段
5. **P4 - 随机暂停**：增加行为随机性

### 监控指标

实施后需要关注：

```python
# 新增监控指标
cdp_download_phase_duration{phase="probe"}
cdp_download_phase_duration{phase="prebuffer"}
cdp_download_phase_duration{phase="playback"}
cdp_download_pause_count
cdp_download_mode{mode="balanced"}
```

### 回滚方案

通过配置切换，无需代码变更：

```bash
# 如果优化后下载过慢，可快速回滚
CDP_DOWNLOAD_MODE=aggressive
```

---

## 相关文件

- `src/downloaders/cdp/audio_downloader.py` - 分片下载实现
- `src/config.py` - 配置定义
- `docs/configuration/downloaders.md` - 下载器配置文档

---

## 更新记录

| 日期 | 内容 |
|------|------|
| 2025-02-01 | 初始版本，记录风控分析和优化思路 |
