# 文档导航

> YouTube Audio API 项目文档索引

## 📚 目录概览

本文档为项目文档提供索引和快速导航。

## 用户指南

面向 API 使用者和运维人员，提供配置和使用指南。

| 文档 | 说明 | 目标受众 |
|------|------|----------|
| [Client Guide](./user-guides/client-guide.md) | API 客户端集成最佳实践，包含同步/异步客户端示例、错误处理、性能优化建议 | API 集成开发者 |
| [WeChat Usage Guide](./user-guides/wechat-usage-guide.md) | 企业微信通知器使用指南，包含消息发送、内容审核、Webhook 池等功能 | 运维人员、开发者 |

## 开发文档

面向项目开发者，提供开发环境搭建、配置优化、功能设计等信息。

| 文档 | 说明 | 目标受众 |
|------|------|----------|
| [Development Guide](./development/development.md) | 开发环境搭建、本地运行、调试技巧 | 开发者 |
| [Docker Optimization](./development/docker-optimization.md) | Docker 镜像体积优化记录，从 882MB 优化到 590-700MB | 运维人员、开发者 |

## 设计文档

面向架构师和高级开发者，记录功能设计决策和架构方案。

| 文档 | 说明 | 目标受众 |
|------|------|----------|
| [Priority Design](./design/priority-design.md) | 任务优先级功能设计文档，包含三级优先级机制、队列调度逻辑 | 架构师、开发者 |
| [Priority Test Report](./design/priority-test-report.md) | 优先级功能测试报告，包含单元测试、集成测试、代码覆盖率 | 测试工程师、开发者 |

## 故障排查

面向开发者和运维人员，记录问题诊断、解决方案、配置分析等。

| 文档 | 说明 | 目标 | 问题类型 |
|------|------|----------|----------|
| [Critical Fix](./troubleshooting/critical-fix.md) | 关键问题修复记录和解决方案 | 生产环境问题 |
| [YouTube Anti-Spam](./troubleshooting/youtube-anti-spam.md) | YouTube 风控机制深度解析，包含威胁图谱、防御策略 | 限流问题 |
| [Your Config Analysis](./troubleshooting/your-config-analysis.md) | 配置问题诊断工具和解决方案说明 | 配置问题 |
| [Config Diagnosis](./troubleshooting/config-diagnosis.md) | 配置诊断脚本说明 | 配置问题 |

## 集成文档

面向开发者，记录第三方服务集成指南。

| 文档 | 说明 | 集成服务 |
|------|------|----------|
| [CDP Sidecar Guide](./integration/cdp-sidecar-guide.md) | CDP Sidecar 开发指导，用于获取 Cookie 和 visitorData | 浏览器自动化 |
| [TikHub API Guide](./integration/tikhub/) | TikHub 集成文档目录 | TikHub API |
| [TikHub API](./integration/tikhub/tikhub-api.md) | TikHub API 完整文档，包含参数解释、请求响应示例 | TikHub API |
| [Audio Download Stalled](./integration/tikhub/audio-download-stall.md) | TikHub 音频下载卡顿问题记录和解决方案 | TikHub 集成 |

## 历史记录

面向开发者和维护人员，记录重要变更和演进。

| 文档 | 说明 | 变更日期 |
|------|------|----------|
| [Retry Policy Change](./history/retry-policy-change.md) | 重试策略从 5 次重试调整为 1 次重试，以及任务优先级队列机制 | 2025-12-14 |
| [Flexible Download Modes](./history/2025-12-13-flexible-download-modes.txt) | 支持灵活的下载模式（仅音频/仅字幕/完整模式） | 2025-12-13 |
| [Adaptive Rate Limiting](./history/2025-12-14-adaptive-rate-limit.txt) | 引入自适应机制应对 YouTube 频控问题 | 2025-12-14 |

## 诊断报告

面向开发者和架构师，记录技术分析、诊断报告、重构总结。

| 文档 | 说明 | 诊断类型 |
|------|------|----------|
| [Changelog](./diagnosis/changelog.md) | ConnectError 修复与文件整理记录 | 修复记录 |
| [Downloader Retry Improvement](./diagnosis/downloader-retry-improvement.md) | 下载器级重试机制改进，包含立即重试 + 指数退避 | 重试机制改进 |
| [Retry Mechanism Analysis](./diagnosis/retry-mechanism-analysis.md) | 原有重试机制分析和问题诊断 | 诊断分析 |
| [TikHub Diagnosis Report](./diagnosis/tikhub-diagnosis-report.md) | TikHub 完整诊断报告，包含连接测试、字幕下载测试 | 集成诊断 |
| [Refactoring Summary](./diagnosis/refactoring-summary.md) | ConnectError 修复 + 下载器重试机制 + 文件结构整理 | 重构总结 |
| [Final Summary](./diagnosis/final-summary.md) | 完整改进总结（ConnectError 修复 + 下载器重试 + 文件整理） | 项目总结 |

## 快速查找

### 按问题类型查找

| 问题类型 | 查看文档 |
|---------|---------|
| 限流问题 | [YouTube Anti-Spam](./troubleshooting/youtube-anti-spam.md), [Your Config Analysis](./troubleshooting/your-config-analysis.md) |
| 重试失败 | [Retry Policy Change](./history/retry-policy-change.md), [Retry Mechanism Analysis](./diagnosis/retry-mechanism-analysis.md) |
| TikHub 集成 | [TikHub API Guide](./integration/tikhub/tikhub-api.md), [TikHub Diagnosis Report](./diagnosis/tikhub-diagnosis-report.md) |
| Docker 优化 | [Docker Optimization](./development/docker-optimization.md) |
| 任务优先级 | [Priority Design](./design/priority-design.md), [Priority Test Report](./design/priority-test-report.md) |
| 配置问题 | [Your Config Analysis](./troubleshooting/your-config-analysis.md), [Config Diagnosis](./troubleshooting/config-diagnosis.md) |
| 通知问题 | [WeChat Usage Guide](./user-guides/wechat-usage-guide.md) |

### 按受众查找

| 目标受众 | 推荐阅读文档 |
|---------|-------------|
| API 集成开发者 | [Client Guide](./user-guides/client-guide.md), [Priority Design](./design/priority-design.md) |
| 运维人员 | [WeChat Usage Guide](./user-guides/wechat-usage-guide.md), [Docker Optimization](./development/docker-optimization.md) |
| 开发者 | [Development Guide](./development/development.md), [Integration Guide](./integration/) |
| 架构师 | [Design](./design/), [Diagnosis Reports](./diagnosis/) |
| 测试工程师 | [Priority Test Report](./design/priority-test-report.md), [Diagnosis Reports](./diagnosis/) |

---

## 维护指南

### 添加新文档

1. 根据文档类型选择合适的目录
2. 使用小写英文文件名，用连字符分隔
3. 更新本导航，在相应章节添加文档链接
4. 保持文档格式一致

### 更新现有文档

1. 保持结构清晰
2. 及时更新日期和版本信息
3. 删除过时内容

### 文档命名规范

- 使用小写英文字母
- 用连字符（-）分隔单词
- 历史文件使用 `YYYY-MM-DD-` 前缀
- 禁止使用中文文件名

---

**最后更新**: 2026-01-27
