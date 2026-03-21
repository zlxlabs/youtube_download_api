# TODOS

## P1 -- 高优先级

### 多 Worker 架构设计与实施
- **What:** 分离 CDP 单线程与 yt-dlp/TikHub 多线程，突破 DOWNLOAD_CONCURRENCY=1 限制
- **Why:** 当前单 Worker 是吞吐量硬天花板，影响服务扩展性。音频下载 120-300s + 等待间隔 60-600s = 每分钟最多处理 1-2 个任务
- **Pros:** 吞吐量提升 3-5x，非 CDP 下载器可并发处理
- **Cons:** 架构改动大，SQLite 并发写入可能成为新瓶颈，CDP 人类行为模拟与多并发的交互复杂
- **Context:** CDP 下载器要求单线程执行（页面冲突），但 yt-dlp 和 TikHub 可以安全并发。方案是 CDP Worker(1个) + Generic Worker(N个) 分离。需要共享任务队列和熔断器状态。SQLite WAL 模式可缓解并发写入问题
- **Effort:** XL (人工 4-6 周) / L (CC ~4-6 小时)
- **Depends on:** 可靠性增强计划完成后（/metrics 提供监控基础）

## P2 -- 中优先级

### 任务取消时优雅中断下载
- **What:** 取消任务时中断正在进行的下载，而非等待下载完成
- **Why:** 当前取消大文件下载可能需要等 5 分钟
- **Pros:** 取消响应更快，释放 Worker 处理其他任务
- **Cons:** 需要修改下载器内部逻辑，增加取消检查点
- **Context:** 当前 `DownloadCancelledError` 只在下载间隙检查，下载过程中无法中断。需要在 curl_cffi 和 yt-dlp 调用中增加进度回调，定期检查取消标志。单 Worker 场景下影响有限，多 Worker 场景下更有价值
- **Effort:** M (人工 1-2 周) / S (CC ~30 分钟)
- **Depends on:** 无，但与多 Worker 架构一起做效果更好

### Grafana 仪表板模板 + 告警规则
- **What:** 提供 Grafana JSON 导入模板，包含下载器成功率、队列深度、熔断状态的可视化面板，以及基础告警规则
- **Why:** 有了 Prometheus /metrics 端点但没有仪表板，监控价值大打折扣
- **Pros:** 运维可视化，问题快速定位，告警自动化
- **Cons:** 需要 Grafana 环境，告警规则需根据实际流量调整阈值
- **Context:** 依赖 /metrics 端点实施完成。建议的告警规则：ip_ban_state >= 1 持续 5 分钟、downloader_success_rate < 0.5、queue_depth > 50
- **Effort:** M (人工 1 周) / S (CC ~15 分钟)
- **Depends on:** 可靠性增强计划中的 Prometheus /metrics 实施
