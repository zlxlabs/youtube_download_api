# 下载器架构详解

本文档详细介绍系统的下载器架构，包括双层缓存策略、场景化优先级策略和扩展性设计。

## 目录

- [架构概览](#架构概览)
- [双层缓存策略](#双层缓存策略)
- [场景化优先级策略](#场景化优先级策略)
- [CDP 下载器内部降级链](#cdp-下载器内部降级链)
- [扩展性设计](#扩展性设计)

---

## 架构概览

### 三层架构

```
┌─────────────────────────────────────────────────────────┐
│                  调用层 (Services/Routes)                │
│  - ManualUploadService: 人工上传                         │
│  - Worker: 下载任务                                      │
└────────────────────┬────────────────────────────────────┘
                      │
┌────────────────────▼────────────────────────────────────┐
│           管理层 (DownloaderManager)                     │
│  统一入口：                                               │
│  - get_metadata()      ← 仅获取元数据                   │
│  - download_resources() ← 下载资源                       │
│  核心职责：                                               │
│  - 优先级选择（可配置）                                   │
│  - 降级策略（自动切换）                                   │
│  - 缓存协调（数据库 + 内存）                              │
│  - 熔断器保护                                             │
│  - 并发控制（防止重复调用）                               │
└────────────────────┬────────────────────────────────────┘
                      │
       ┌──────────────┼────────────┬────────────┐
       │              │            │            │
┌─────▼──────┐ ┌──────▼──────┐ ┌──▼─────┐ ┌────▼──────┐
│ CDPDown.   │ │ YtdlpDown.  │ │TikHub  │ │ [新下载器]│
│ (真实浏览器 │ │ (免费)      │ │(付费)  │ │ (扩展)    │
│  指纹，音频 │ │             │ │        │ │           │
│  最高优先级)│ │             │ │        │ │           │
└────────────┘ └─────────────┘ └────────┘ └───────────┘
```

其中 `CDPDownloader`（`src/downloaders/cdp/downloader.py`）是当前 `AUDIO_DOWNLOAD_PRIORITY` 中优先级最高的下载器：通过真实浏览器（Chrome DevTools Protocol）获取音频下载所需的 cookies/headers，指纹更接近真人访问，降低触发 403 风控的概率。

---

## 双层缓存策略

系统使用两层缓存优化性能和成本。

### 缓存类型

| 缓存类型 | 存储位置 | TTL | 用途 | 优势 |
|---------|---------|-----|------|------|
| **元数据缓存** | 数据库 (video_resources) | 永久 | 避免重复元数据获取 | 永久有效，跨任务共享 |
| **API 响应缓存** | 内存 (TTLCache) | 3小时 | 复用 TikHub 下载链接 | 短期内无需重复 API 调用 |

### 缓存流程示例

```
首次获取元数据：
  → DownloaderManager.get_metadata()
  → 数据库未命中
  → ytdlp API 调用（免费，1-2秒）
  → 写入数据库（永久保存）

重复获取元数据：
  → DownloaderManager.get_metadata()
  → 数据库命中！
  → 直接返回（免费，5ms）
  → 性能提升：200-400倍
```

---

## 场景化优先级策略

系统针对不同场景使用不同的下载器优先级，平衡成本、性能和稳定性。

### 场景 1：仅获取元数据

**配置**: `METADATA_PRIORITY=ytdlp,tikhub`
**策略**: 优先免费方案，节省成本

```
用途：人工上传时获取视频标题、作者等信息
流程：
  1. 检查数据库缓存 → 命中则直接返回（5ms）
  2. 尝试 ytdlp（免费） → 成功率 80%
  3. 降级 tikhub（$0.002） → 成功率 95%
  4. 写入数据库（永久保存）

成本：首次 $0.0004/个（平均）
      重复：$0（数据库缓存）
```

### 场景 2：音频 + 字幕（完整模式）

**配置**: `AUDIO_DOWNLOAD_PRIORITY=cdp,ytdlp,tikhub`
**策略**: CDP 优先（真实浏览器指纹，降低风控风险），失败降级 ytdlp，再降级 tikhub

```
特点：
  - 大文件下载（30-60秒）
  - 风控风险高
  - CDP 优先（真实浏览器指纹），失败依次降级到免费/付费方案兜底

流程：
  1. 元数据从数据库读取（复用缓存）
  2. 尝试 cdp 下载音频（内部还有自己的降级链，见下一节）+ 字幕
  3. 失败 → 降级 ytdlp
  4. 仍失败 → 降级 tikhub
  5. 支持部分成功（字幕成功，音频失败）
```

### 场景 3：仅字幕 ⭐

**配置**: `TRANSCRIPT_ONLY_PRIORITY=tikhub,ytdlp`
**策略**: **优先 TikHub**（与其他场景相反）

```
为什么优先 TikHub？
  ✓ 字幕获取是轻量级操作（API 调用，无大文件）
  ✓ TikHub 更稳定，风控风险低
  ✓ 不涉及大文件下载，不暴露本地 IP
  ✓ 成本可接受：$0.002/次
  ✓ 避免 ytdlp 字幕失败 → 自动下载音频（浪费）

流程：
  1. 尝试 tikhub 获取字幕（$0.002）
  2. 检查内存缓存（3小时TTL）→ 可能命中（$0）
  3. 失败 → 降级 ytdlp

特殊逻辑（ytdlp）：
  - 如果无字幕 → 自动下载音频作为 fallback
  - result.audio_fallback = true
```

> 注：CDP 不参与本场景。CDP 的字幕能力是音频下载的副产品（下载音频时顺带调用 `download_subtitle`），仅字幕场景没有音频下载动作可以"顺带"，因此 CDP 不出现在 `TRANSCRIPT_ONLY_PRIORITY` 中。

---

## CDP 下载器内部降级链

CDP 自己也是一个多层降级下载器（`src/downloaders/cdp/audio_downloader.py` 的 `AudioDownloader.download_audio`），在被 `AUDIO_DOWNLOAD_PRIORITY` 选中之后，内部还有一条独立的降级链，不需要靠 manager 层再降级到 ytdlp/tikhub 才能兜底：

```
音频下载请求
  │
  │  首次请求永远不带 Cookie（生产 403 取证后确定的最保守策略，降低账号风控暴露）
  ▼
1. curl_cffi 分片下载（cdp_enable_multipart 开启 + 文件大小 ≥ cdp_multipart_min_size 时触发）
   │
   ├─ 成功 → 返回
   │
   └─ 403 → 检查是否有命中目标 URL 域名的浏览器 cookie（Network.getAllCookies 捕获，
             按 RFC6265 简化域匹配规则筛选）
             ├─ 命中 → 阶段级"带 cookie 重试"一次（不与分片内部自身的重试次数叠加）
             │         ├─ 成功 → 返回
             │         └─ 失败 → 降级到步骤 2
             └─ 无可用 cookie → 直接降级到步骤 2
  ▼
2. curl_cffi 单线程下载（TLS 指纹模拟 + 真实 headers）
   │
   ├─ 成功 → 返回
   │
   └─ 403 → 同样规则：命中 cookie 则重试一次，仍失败/无 cookie → 降级到步骤 3
  ▼
3. CDN 节点切换（解析 googlevideo.com URL 的 mn 参数，依次尝试其它候选节点）
   │
   ├─ 成功 → 返回
   └─ 全部失败 → 降级到步骤 4
  ▼
4. yt-dlp 直接下载（兜底，使用 cookies）
   │
   ├─ 成功 → 返回
   └─ 失败 → 抛出 DownloaderError，交给 manager 层降级到 ytdlp/tikhub
```

关键约束（`_maybe_retry_with_cookie` 方法）：
- **每个阶段最多一次**带 cookie 重试，不是无限重试，也不与分片下载内部自身的重试矩阵叠加。
- 这是相对保守、只在明确遇到 403 时才触发的策略，不影响正常无 403 场景的行为——`cookies` 参数为空时，重试逻辑直接跳过，零行为变化。
- `download_audio` 入口处会用 `_sanitize_download_headers` 无条件剥离 headers 里的 Cookie（大小写不敏感），保证首次请求、CDN 节点切换请求都不会带上 `quick_fetch_data` 可能被动捕获到的真实浏览器 Cookie；`_maybe_retry_with_cookie` 里"原始 headers 无 Cookie"的判断因此在正常路径下恒成立，仅作为防御性检查保留（防止未来 headers 来源变化绕过剥离）。真正用于重试的 Cookie 头完全基于 cookie jar 域匹配重新构造（`_build_cookie_header`），在剥离之后通过 `override_cookie` 参数重新注入，不受剥离影响。

---

## 扩展性设计

系统架构支持无限扩展下载器，添加新下载器仅需 5 步。

### 步骤 1：创建下载器类

```python
# src/downloaders/new_downloader.py
from src.downloaders.base import BaseDownloader

class NewDownloader(BaseDownloader):
    @property
    def name(self) -> str:
        return "newapi"

    @property
    def is_available(self) -> bool:
        return bool(self.api_key)

    async def fetch_metadata(self, video_url, video_id):
        # 实现元数据获取
        pass

    async def download_resources(self, ...):
        # 实现资源下载
        pass
```

### 步骤 2：注册到管理器

```python
# src/downloaders/manager.py
from .new_downloader import NewDownloader

class DownloaderManager:
    def _init_downloaders(self):
        # ...
        elif name == "newapi":
            downloader = NewDownloader(self.settings)
            # ...
```

### 步骤 3-5：配置、文档、测试

```bash
# config.py
NEWAPI_API_KEY: Optional[str] = None

# .env.example
NEWAPI_API_KEY=your-key-here
METADATA_PRIORITY=newapi,ytdlp,tikhub

# tests/test_new_downloader.py
async def test_fetch_metadata():
    # 测试新下载器
    pass
```

### 扩展示例

```
添加专门的元数据 API（免费且快速）

METADATA_PRIORITY=metaapi,ytdlp,tikhub

metaapi: 0.3秒，免费
ytdlp:  1-2秒，免费
tikhub: 0.5秒，$0.002
```

---

## 相关文档

- [配置总览](../configuration/overview.md)
- [下载器配置](../configuration/downloaders.md)
- [架构概览](./overview.md)
