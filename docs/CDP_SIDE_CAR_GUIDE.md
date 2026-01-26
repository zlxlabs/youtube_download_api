# CDP Sidecar 开发指导

本文档用于指导后续开发与验证：通过 CDP 获取新鲜 Cookie/visitorData，结合 `bgutil-ytdlp-pot-provider` 生成 PO Token，以最大化 `yt-dlp` 下载成功率。

## 现状结论（已验证）

- CDP 可以稳定获取 `youtube.com`/`google.com` 的完整 Cookie 集，以及 `VISITOR_DATA`。
- 在当前 Chrome 登录态下，`youtubei/v1/player` 可返回 `playabilityStatus=OK`，但响应中未包含 `serviceIntegrityDimensions.poToken`。
- 因此：**CDP 本身无法直接产出 `poToken`**，仍需使用 `bgutil-ytdlp-pot-provider` 生成并注入给 `yt-dlp`。
- 目前流程在 **“获取播放信息”** 可成功，但在 **“实际拉取音频流”** 可能返回 `HTTP 403`，这通常与 IP 信誉/风控有关。

## 目标架构

```
Chrome (CDP) ——> Cookie + visitorData
      |
      v
yt-dlp + bgutil POT Provider ——> 下载音频
```

关键原则：
- **Cookie 与下载出口 IP 一致**（同一机器或同一代理出口）。
- **Cookie 尽量新鲜**：在每次下载前刷新获取。
- **poToken 由 bgutil 生成**，CDP 不负责生成。

## 推荐开发路径

### 阶段 1：CDP 导出 Cookie（脚本）

目标：将 CDP 中的 Cookie 导出为 Netscape 格式文件，供 `yt-dlp` 直接使用。

要点：
- 使用 `Network.getAllCookies` 获取 `youtube.com` 和 `google.com` 的 Cookie。
- 写入 `data/cdp_cookies.txt`。
- 单独输出 `VISITOR_DATA`（可用于调试或后续扩展）。

### 阶段 2：CDP Sidecar 服务

目标：每次下载任务前即时刷新 Cookie，避免 Cookie 过期或 IP 不匹配。

建议流程：
1. 通过 CDP 连接浏览器标签页（目标视频页）。
2. `Network.getAllCookies` 导出 Cookie。
3. 将 Cookie 写入固定路径（如 `data/cdp_cookies.txt`）。
4. 主进程读取该 cookie file 进行下载。

可选增强：
- 对接 `visitorData`，记录到日志便于排查。
- 允许 Sidecar 自己打开/刷新 YouTube 页面（确保 Cookie 新鲜）。

### 阶段 3：与主下载流程集成

主流程建议顺序：
1. Sidecar 刷新 Cookie。
2. `yt-dlp` 获取 info（`extract_info`）。
3. `bgutil` 生成 `poToken`。
4. `yt-dlp` 下载音频。

保持 `player_client` 的优先级：
- 有 Cookie：`tv_embedded` -> `web_creator`
- 无 Cookie：`tv_embedded` -> `ios` -> `web_creator`

## 核心配置清单

建议在运行时确保：
- `COOKIE_FILE=./data/cdp_cookies.txt`
- `POT_SERVER_URL=http://localhost:4416`
- 代理与 Chrome 出口 IP 一致（必要时配置）

## 测试步骤（建议）

1. **确认 pot-provider 可用**
   - `GET http://127.0.0.1:4416/ping` 返回 `200`

2. **CDP 导出 Cookie**
   - 生成 `data/cdp_cookies.txt`

3. **只取信息（info-only）**
   - `yt-dlp` 能成功 `extract_info` 并触发 `poToken` 生成

4. **完整下载**
   - 若出现 403，优先检查 IP 信誉/代理一致性

## 常见问题与排查

- **INFO 成功、下载 403**
  - 多半与 IP 信誉有关（数据中心 IP 易被封）
  - 解决思路：使用住宅代理，或确保 Chrome 与下载器共用同一出口

- **CDP 直连下载得到的不是媒体文件**
  - YouTube 当前使用 MSE/Blob + SABR 流控，`video.currentSrc` 为 `blob:`，CDP 常见捕获到的是 `application/vnd.yt-ump`（播放清单/控制流），而非音视频流本体
  - 要完全复刻 IDM，需要拦截浏览器内部的媒体分片请求（通常依赖扩展的 `webRequest`/`debugger` 权限）

- **poToken 取不到**
  - CDP 无法直接取到 `poToken` 是正常现象
  - 继续依赖 `bgutil` 生成即可

- **Cookie 有效但仍触发 LOGIN_REQUIRED**
  - 检查 Cookie 是否与出口 IP 一致
  - 尝试在同一出口 IP 下重新登录并刷新 Cookie

## 下一步建议

- 落地 Sidecar 服务（常驻或按需启动），并让下载任务在执行前先刷新 Cookie。
- 将 Sidecar 的 Cookie 文件路径写入 `.env` 或配置中心。
- 若仍有 403，优先从 IP/代理策略入手。
