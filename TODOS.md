# TODOS

## P2 -- 中优先级

### 任务取消时优雅中断下载
- **What:** 取消任务时中断正在进行的下载，而非等待下载完成
- **Why:** 当前取消大文件下载可能需要等 5 分钟
- **Pros:** 取消响应更快，释放 Worker 处理其他任务
- **Cons:** 需要修改下载器内部逻辑，增加取消检查点
- **Context:** 当前 `DownloadCancelledError` 只在下载间隙检查，下载过程中无法中断。需要在 curl_cffi 和 yt-dlp 调用中增加进度回调，定期检查取消标志。单用户场景下影响有限
- **Effort:** M (人工 1-2 周) / S (CC ~30 分钟)
- **Depends on:** 无

## P3 -- 低优先级（远期/有需求再说）

### Grafana 仪表板模板 + 告警规则
- **What:** Prometheus + Grafana 统一监控仪表板
- **Why:** /metrics 端点已预埋，未来多项目统一监控时可直接接入
- **Context:** 当前单用户场景下，企微报警 + Agent 查日志已满足需求。等有多人协作或需要趋势分析时再搭建
- **Effort:** M (人工 1 周) / S (CC ~15 分钟)
- **Depends on:** Prometheus + Grafana 环境就绪

### 多 Worker 架构设计与实施
- **What:** 分离 CDP 单线程与 yt-dlp/TikHub 多线程，突破 DOWNLOAD_CONCURRENCY=1 限制
- **Why:** 当前单 Worker 是吞吐量天花板，但单用户 + 单 IP 场景下并发越高封禁越快
- **Context:** 仅在引入代理池/多 IP 后才值得实施
- **Effort:** XL (人工 4-6 周) / L (CC ~4-6 小时)
- **Depends on:** 代理池/多 IP 方案
