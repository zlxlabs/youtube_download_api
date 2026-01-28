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
| [Video Info API](./design/video-info-api.md) | 视频元数据 API 设计文档，包含数据库缓存、智能降级、YouTube Data API 集成 | 架构师、开发者 |
| [Admin UI Redesign](./design/admin-ui-redesign.md) | 管理界面重构设计，包含 Tab 导航、任务队列、视频资源管理、Cookie 管理等功能 | 前端开发者、UI/UX 设计师 |
| [Metadata Cache Optimization](./design/metadata-cache-optimization.md) | 元数据缓存优化方案，包含双层缓存策略、性能分析 | 架构师、开发者 |

## 集成文档

面向开发者，记录第三方服务集成指南。

| 文档 | 说明 | 集成服务 |
|------|------|----------|
| [YouTube Data API](./integration/youtube-data-api.md) | YouTube Data API v3 集成说明，包含 API Key 申请、配置示例、配额管理 | YouTube Data API v3 |
| [TikHub API](./integration/tikhub-api.md) | TikHub API 集成文档，包含字幕获取、音频下载、参数说明 | TikHub API |
| [CDP Sidecar Guide](./integration/cdp-sidecar-guide.md) | CDP Sidecar 开发指导，用于获取 Cookie 和 visitorData | 浏览器自动化 |
| [Audio Download Stall](./troubleshooting/audio-download-stall.md) | TikHub 音频下载卡顿问题记录和解决方案 | TikHub 集成 |

## 故障排查

面向开发者和运维人员，记录问题诊断、解决方案、配置分析等。

| 文档 | 说明 | 目标 | 问题类型 |
|------|------|----------|----------|
| [Rate Limiting](./troubleshooting/rate-limiting.md) | YouTube 风控机制深度解析，包含威胁图谱、防御策略、IP 信誉评分 | 限流问题 |
| [Audio Download Stall](./troubleshooting/audio-download-stall.md) | TikHub 音频下载卡顿问题记录和解决方案，包含 Range 分段下载实现 | 下载卡顿 |
| [Config Diagnosis](./troubleshooting/config-diagnosis.md) | 配置诊断脚本说明 | 配置问题 |

## 项目文档

面向项目相关人员，记录项目需求、架构概览、变更日志等。

| 文档 | 说明 | 目标受众 |
|------|------|----------|
| [Project Requirements](./project/requirements.md) | 项目核心需求、背景信息、功能特性说明 | 项目经理、开发者 |
| [Project Architecture](./project/architecture.md) | 系统架构概览，包含模块划分、技术栈、数据流 | 架构师、开发者 |
| [Changelog](./project/changelog.md) | 项目变更日志，记录重要功能更新、Bug 修复、架构调整 | 所有相关人员 |

## 历史记录

面向开发者和维护人员，记录重要变更和演进。

| 文档 | 说明 | 变更日期 |
|------|------|----------|
| [Retry Policy Change](./history/retry-policy-change.md) | 重试策略从 5 次重试调整为 1 次重试，以及任务优先级队列机制 | 2025-12-14 |
| [Flexible Download Modes](./history/2025-12-13-flexible-download-modes.txt) | 支持灵活的下载模式（仅音频/仅字幕/完整模式） | 2025-12-13 |
| [Adaptive Rate Limiting](./history/2025-12-14-adaptive-rate-limit.txt) | 引入自适应机制应对 YouTube 风控问题 | 2025-12-14 |

### 诊断报告归档

历史诊断报告已归档到 `history/diagnosis-archive/` 目录。

| 文档 | 说明 | 诊断类型 |
|------|------|----------|
| [Final Summary](./history/diagnosis-archive/final-summary.md) | 完整改进总结（ConnectError 修复 + 下载器重试 + 文件整理） | 项目总结 |
| [Downloader Retry Improvement](./history/diagnosis-archive/downloader-retry-improvement.md) | 下载器级重试机制改进，包含立即重试 + 指数退避 | 重试机制改进 |
| [Retry Mechanism Analysis](./history/diagnosis-archive/retry-mechanism-analysis.md) | 原有重试机制分析和问题诊断 | 诊断分析 |
| [TikHub Diagnosis Report](./history/diagnosis-archive/tikhub-diagnosis-report.md) | TikHub 完整诊断报告，包含连接测试、字幕下载测试 | 集成诊断 |
| [Refactoring Summary](./history/diagnosis-archive/refactoring-summary.md) | ConnectError 修复 + 下载器重试机制 + 文件结构整理 | 重构总结 |
| [Changelog](./history/diagnosis-archive/changelog.md) | ConnectError 修复与文件整理记录 | 修复记录 |

## 快速查找

### 按问题类型查找

| 问题类型 | 查看文档 |
|---------|---------|
| 限流问题 | [Rate Limiting](./troubleshooting/rate-limiting.md) |
| 重试失败 | [Retry Policy Change](./history/retry-policy-change.md), [Retry Mechanism Analysis](./history/diagnosis-archive/retry-mechanism-analysis.md) |
| TikHub 集成 | [TikHub API](./integration/tikhub-api.md), [Audio Download Stall](./troubleshooting/audio-download-stall.md) |
| Docker 优化 | [Docker Optimization](./development/docker-optimization.md) |
| 任务优先级 | [Priority Design](./design/priority-design.md), [Priority Test Report](./design/priority-test-report.md) |
| 配置问题 | [Config Diagnosis](./troubleshooting/config-diagnosis.md) |
| 通知问题 | [WeChat Usage Guide](./user-guides/wechat-usage-guide.md) |
| YouTube Data API | [YouTube Data API](./integration/youtube-data-api.md) |
| 视频元数据 API | [Video Info API](./design/video-info-api.md) |

### 按受众查找

| 目标受众 | 推荐阅读文档 |
|---------|-------------|
| API 集成开发者 | [Client Guide](./user-guides/client-guide.md), [Priority Design](./design/priority-design.md), [Video Info API](./design/video-info-api.md) |
| 运维人员 | [WeChat Usage Guide](./user-guides/wechat-usage-guide.md), [Docker Optimization](./development/docker-optimization.md), [Rate Limiting](./troubleshooting/rate-limiting.md) |
| 开发者 | [Development Guide](./development/development.md), [Integration Guides](./integration/) |
| 架构师 | [Design](./design/), [Project Architecture](./project/architecture.md) |
| 测试工程师 | [Priority Test Report](./design/priority-test-report.md), [Diagnosis Reports](./history/diagnosis-archive/) |
| 项目经理 | [Project Requirements](./project/requirements.md), [Changelog](./project/changelog.md) |

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

### 文档放置原则

- **design/**：功能设计、架构方案
- **integration/**：第三方服务集成
- **troubleshooting/**：故障排查、问题解决
- **history/**：历史变更记录、归档文档
- **user-guides/**：面向用户的指南
- **development/**：开发环境搭建、调试技巧
- **project/**：项目需求、架构概览、变更日志

---

**最后更新**: 2026-01-28
