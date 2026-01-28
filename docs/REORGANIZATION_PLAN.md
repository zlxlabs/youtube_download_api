# Docs/ 文件夹整理方案

> 创建时间：2026-01-28
> 目标：优化文档结构，提高可维护性

## 当前问题分析

### 1. 根目录文档散落
- 多个设计文档（VIDEO_INFO_API_*.md）散落在根目录
- 配置文档（YOUTUBE_DATA_API_SETUP.md、tikhub_api.md）未分类
- 实施文档（VIDEO_INFO_API_IMPLEMENTATION.md）与设计文档混杂

### 2. 历史文档未及时归档
- history/ 目录只有 1 个文件
- diagnosis/ 目录中包含大量历史记录
- troubleshooting/ 中的文档时效性不同

### 3. 重复内容
- VIDEO_INFO_API_DESIGN.md（870 行）和 VIDEO_INFO_API_SIMPLE.md（437 行）内容高度重复
- VIDEO_INFO_API_IMPLEMENTATION.md 可合并到设计文档

### 4. 命名不规范
- `项目需求.md` 使用中文文件名
- `tikhub_api.md` 命名不够明确

---

## 整理方案

### 方案 A：最小改动（保守）

#### 1. 合并重复文档
```
保留：VIDEO_INFO_API_DESIGN.md（完整版本）
删除：
  - VIDEO_INFO_API_SIMPLE.md
  - VIDEO_INFO_API_IMPLEMENTATION.md
```

#### 2. 移动散落文档
```
移动到 design/:
  - VIDEO_INFO_API_DESIGN.md → design/video-info-api-design.md

移动到 integration/:
  - YOUTUBE_DATA_API_SETUP.md → integration/youtube-data-api-guide.md
  - tikhub_api.md → integration/tikhub-api.md
  - cdp-sidecar-guide.md → integration/cdp-sidecar-guide.md

移动到 troubleshooting/:
  - audio_download_stall.md → troubleshooting/audio-download-stall.md

移动到 design/:
  - admin-ui-redesign.md → design/admin-ui-redesign.md

重命名：
  - 项目需求.md → project-requirements.md
```

#### 3. 更新 README.md 链接
- 更新所有文档路径引用
- 添加新增文档的导航条目

---

### 方案 B：彻底重组（推荐）

#### 新目录结构
```
docs/
├── README.md                      # 文档导航（保持不变）
│
├── user-guides/                   # 用户指南（保持不变）
│   ├── client-guide.md
│   └── wechat-usage-guide.md
│
├── development/                   # 开发文档
│   ├── development.md
│   ├── docker-optimization.md
│   └── testing.md                 # 新增：测试指南
│
├── design/                        # 设计文档（合并）
│   ├── priority-design.md
│   ├── priority-test-report.md
│   ├── video-info-api.md          # 合并：VIDEO_INFO_API_DESIGN.md
│   ├── admin-ui-redesign.md
│   └── metadata-cache-optimization.md
│
├── integration/                   # 集成文档（新增）
│   ├── youtube-data-api.md        # 重命名：YOUTUBE_DATA_API_SETUP.md
│   ├── tikhub-api.md              # 重命名：tikhub_api.md
│   ├── cdp-sidecar-guide.md
│   └── cookie-management.md       # 新增：Cookie 管理综合指南
│
├── troubleshooting/               # 故障排查（精简）
│   ├── rate-limiting.md           # 重命名：youtube-anti-spam.md
│   ├── config-issues.md           # 合并：your-config-analysis.md + critical-fix.md
│   └── audio-download-stall.md    # 移动：audio_download_stall.md
│
├── history/                       # 历史记录（归档）
│   ├── retry-policy-change.md
│   └── downloader-improvements/  # 新增：下载器改进归档
│       ├── connecterror-fix.md
│       ├── retry-mechanism.md
│       └── final-summary.md
│
└── project/                       # 项目相关（新增）
    ├── requirements.md            # 重命名：项目需求.md
    ├── architecture.md            # 新增：架构概览
    └── changelog.md              # 新增：项目变更日志
```

---

### 方案 C：归档优先（激进）

#### 1. 大幅归档历史文档
```
将 diagnosis/ 目录全部移动到 history/diagnosis-archive/
```

#### 2. 精简 troubleshooting/
```
只保留最新的、通用的故障排查指南
将具体的诊断报告移动到 history/
```

---

## 推荐方案：方案 B（彻底重组）

### 理由

1. **清晰分类**：按受众和用途分类，便于查找
2. **避免重复**：合并重复内容，减少维护成本
3. **规范命名**：统一使用英文文件名，小写连字符
4. **历史归档**：将历史记录集中管理，不干扰当前文档
5. **扩展性好**：新文档有明确的放置位置

---

## 执行清单（方案 B）

### 阶段 1：移动和重命名文档

#### design/ 目录
```bash
# 移动并重命名
git mv docs/VIDEO_INFO_API_DESIGN.md docs/design/video-info-api.md
git mv docs/admin-ui-redesign.md docs/design/admin-ui-redesign.md

# 删除重复文档
git rm docs/VIDEO_INFO_API_SIMPLE.md
git rm docs/VIDEO_INFO_API_IMPLEMENTATION.md
```

#### integration/ 目录
```bash
# 创建目录
mkdir -p docs/integration

# 移动并重命名
git mv docs/YOUTUBE_DATA_API_SETUP.md docs/integration/youtube-data-api.md
git mv docs/tikhub_api.md docs/integration/tikhub-api.md
git mv docs/cdp-sidecar-guide.md docs/integration/cdp-sidecar-guide.md
```

#### troubleshooting/ 目录
```bash
# 移动并重命名
git mv docs/audio_download_stall.md docs/troubleshooting/audio-download-stall.md

# 重命名现有文档
git mv docs/troubleshooting/youtube-anti-spam.md docs/troubleshooting/rate-limiting.md
```

#### history/ 目录
```bash
# 创建子目录
mkdir -p history/diagnosis-archive

# 移动历史诊断报告
git mv docs/diagnosis/changelog.md history/diagnosis-archive/
git mv docs/diagnosis/downloader-retry-improvement.md history/diagnosis-archive/
git mv docs/diagnosis/retry-mechanism-analysis.md history/diagnosis-archive/
git mv docs/diagnosis/refactoring-summary.md history/diagnosis-archive/
git mv docs/diagnosis/tikhub-diagnosis-report.md history/diagnosis-archive/
git mv docs/diagnosis/final-summary.md history/diagnosis-archive/

# 删除空目录
git rm -r docs/diagnosis/
```

#### project/ 目录（新建）
```bash
# 创建目录
mkdir -p docs/project

# 移动并重命名
git mv docs/项目需求.md docs/project/requirements.md
```

### 阶段 2：删除文档
```bash
git rm docs/troubleshooting/critical-fix.md
git rm docs/troubleshooting/your-config-analysis.md
```

### 阶段 3：更新 README.md

需要更新以下内容：
1. **新增章节**：集成文档、项目文档
2. **更新链接**：所有移动文档的路径
3. **删除链接**：已删除文档的引用

### 阶段 4：验证
```bash
# 检查所有链接是否有效
grep -r "\.md" docs/README.md

# 检查文档中是否有互相引用
grep -r "\.md" docs/
```

---

## 实施优先级

| 优先级 | 操作 | 说明 |
|-------|------|------|
| P0 | 合并 VIDEO_INFO_API 重复文档 | 消除最明显的重复 |
| P0 | 移动散落文档到正确目录 | 改善整体结构 |
| P1 | 归档历史诊断报告 | 清理历史文档 |
| P1 | 规范化文件命名 | 统一命名风格 |
| P2 | 新增综合指南 | 提升文档质量 |
| P2 | 完善 README.md 导航 | 改善查找体验 |

---

## 注意事项

1. **Git 操作**：使用 `git mv` 而不是 `mv`，保留历史记录
2. **链接更新**：确保 README.md 中的所有链接都已更新
3. **交叉引用**：检查文档中是否有互相引用，需要同步更新
4. **测试环境**：先在测试分支执行，验证无误后再合并到主分支
5. **文档内链接**：检查文档内部的相对路径链接是否需要更新

---

## 后续维护建议

1. **文档命名规范**：
   - 使用英文文件名
   - 小写字母
   - 连字符分隔单词
   - 避免中文和特殊字符

2. **文档放置原则**：
   - design/：功能设计、架构方案
   - integration/：第三方服务集成
   - troubleshooting/：故障排查、问题解决
   - history/：历史变更记录、归档文档
   - user-guides/：面向用户的指南
   - development/：开发环境搭建、调试技巧

3. **定期清理**：
   - 每季度检查一次历史文档
   - 及时归档过时文档
   - 删除重复或无价值文档

4. **文档维护**：
   - 新增功能时同步更新文档
   - 每次重大变更后更新相关章节
   - 定期检查文档准确性

---

## 预期效果

完成整理后：

✅ **文档结构清晰**：按类型和受众分类，便于查找
✅ **避免内容重复**：减少维护成本
✅ **历史文档归档**：不干扰当前文档
✅ **命名规范统一**：便于代码审查和维护
✅ **导航体验优化**：README.md 导航更加清晰
✅ **可扩展性好**：新文档有明确的放置位置

---

**是否开始执行方案 B？**
