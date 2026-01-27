# 管理界面重构设计文档

## 一、需求概述

### 背景
当前项目只有一个 `/admin` 页面，专注于人工上传功能。需要将其升级为统一的管理中心，支持：
- 实时任务队列监控
- 任务历史查询
- 视频资源管理
- 创建任务和文件上传

### 定位
Web UI 是辅助性功能，主要用于：
- 文件上传
- 临时性管理
- 查看任务进度
- 简单的资源浏览

**不追求**：复杂交互、实时更新、批量操作

---

## 二、架构设计

### 2.1 前端结构

```
┌─────────────────────────────────────────────────────┐
│  顶部栏：API Key 配置 | 刷新按钮                       │
├─────────────────────────────────────────────────────┤
│  Tab导航：[ 队列 | 任务历史 | 视频资源 | 创建/上传 ]   │
├─────────────────────────────────────────────────────┤
│                                                       │
│  主内容区（根据 Tab 切换显示不同内容）                  │
│                                                       │
└─────────────────────────────────────────────────────┘
```

### 2.2 Tab 功能说明

#### Tab 1: 任务队列
- **Pending 任务**
  - 显示：video_id、标题、优先级标签(urgent/normal)、队列位置、预计等待时间、任务类型
  - 操作：取消任务
- **Downloading 任务**
  - 显示：当前正在下载的任务
  - 状态：高亮"进行中"

#### Tab 2: 任务历史
- **筛选器**
  - 状态：completed/failed/cancelled
  - 日期范围：开始日期 ~ 结束日期
  - 搜索：video_id 或标题（模糊匹配）
- **列表显示**
  - 字段：video_id、标题、状态、任务类型、缓存命中、创建时间、完成时间
  - 失败任务显示错误信息
- **操作**
  - 查看详情：弹窗显示文件链接、元数据、错误信息
- **分页**：上一页/下一页

#### Tab 3: 视频资源库
- **搜索框**：按 video_id 或标题搜索
- **列表显示**
  - 字段：video_id、缩略图、标题、作者、文件数量（音频/字幕）、来源标签(auto/manual)、创建时间
- **操作**
  - 查看详情：弹窗显示完整元数据、文件列表、相关任务历史
  - 删除资源：级联删除所有文件，保留任务记录
- **分页**：上一页/下一页

#### Tab 4: 创建/上传
- **区域 A：创建下载任务**
  - 输入：video_url、priority(urgent/normal)、include_audio、include_transcript
  - 操作：提交创建
- **区域 B：人工上传**（复用现有功能）
  - 输入：video_url、文件、可选元数据
  - 操作：上传文件

### 2.3 技术栈

- **前端**：原生 HTML + CSS + JavaScript（无依赖）
- **UI 风格**：简洁风格，CSS Grid/Flexbox 布局
- **数据交互**：Fetch API
- **状态管理**：全局变量 + LocalStorage（保存 API Key）

---

## 三、后端 API 设计

### 3.1 新增 API

#### 3.1.1 列出视频资源

**请求**
```
GET /api/v1/video-resources?search=<keyword>&limit=20&offset=0
```

**查询参数**
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| search | string | 否 | 搜索 video_id 或标题（模糊匹配） |
| limit | integer | 否 | 每页数量，默认 20，最大 100 |
| offset | integer | 否 | 偏移量，默认 0 |

**响应**
```json
{
  "items": [
    {
      "video_id": "dQw4w9WgXcQ",
      "video_info": {
        "title": "Rick Astley - Never Gonna Give You Up",
        "author": "Rick Astley",
        "thumbnail": "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg",
        "duration": 213,
        "channel_id": "UCuAXFkgsw1L7xaCfnd5JJOw",
        "description": "...",
        "upload_date": "20091025",
        "view_count": 1500000000
      },
      "files_count": {
        "audio": 1,
        "transcript": 1
      },
      "upload_source": "auto",
      "created_at": "2025-12-12T10:00:00Z"
    }
  ],
  "total": 42,
  "limit": 20,
  "offset": 0
}
```

**字段说明**
- `upload_source`: "auto"（自动下载）或 "manual"（人工上传）
  - 判断逻辑：如果视频的任意一个文件是 manual 上传，则标记为 manual

---

#### 3.1.2 获取视频资源详情

**请求**
```
GET /api/v1/video-resources/{video_id}
```

**响应**
```json
{
  "video_id": "dQw4w9WgXcQ",
  "video_info": {
    "title": "Rick Astley - Never Gonna Give You Up",
    "author": "Rick Astley",
    "thumbnail": "https://...",
    "duration": 213,
    "channel_id": "UCuAXFkgsw1L7xaCfnd5JJOw",
    "description": "...",
    "upload_date": "20091025",
    "view_count": 1500000000
  },
  "files": [
    {
      "id": "abc123",
      "file_type": "audio",
      "url": "/api/v1/files/abc123.m4a",
      "size": 3456789,
      "format": "m4a",
      "upload_source": "auto",
      "original_format": null,
      "created_at": "2025-12-12T10:00:00Z",
      "expires_at": "2025-02-10T10:00:00Z"
    },
    {
      "id": "def456",
      "file_type": "transcript",
      "url": "/api/v1/files/def456.srt",
      "size": 12345,
      "format": "srt",
      "language": "en",
      "upload_source": "auto",
      "original_format": null,
      "created_at": "2025-12-12T10:00:00Z",
      "expires_at": "2025-02-10T10:00:00Z"
    }
  ],
  "related_tasks": [
    {
      "task_id": "550e8400-e29b-41d4-a716-446655440000",
      "status": "completed",
      "priority": "normal",
      "created_at": "2025-12-12T10:00:00Z",
      "completed_at": "2025-12-12T10:01:30Z"
    }
  ],
  "created_at": "2025-12-12T10:00:00Z",
  "updated_at": "2025-12-12T10:01:30Z"
}
```

---

#### 3.1.3 删除视频资源

**请求**
```
DELETE /api/v1/video-resources/{video_id}
```

**响应**
```json
{
  "success": true,
  "deleted_files": 2,
  "message": "Video resource and 2 files deleted successfully"
}
```

**行为**
1. 删除 `video_resources` 表记录
2. 删除所有关联的 `files` 表记录
3. 物理删除所有关联的文件
4. **保留** `tasks` 表记录（审计日志）

**错误响应**
```json
{
  "detail": "Video resource not found"
}
```

---

### 3.2 增强现有 API

#### 3.2.1 任务列表增强

**原有接口**
```
GET /api/v1/tasks?status=<status>&limit=20&offset=0
```

**新增查询参数**
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| search | string | 否 | 搜索 video_id 或标题（模糊匹配） |
| created_after | string | 否 | 创建时间起点，ISO 8601 格式，如 "2025-01-01T00:00:00Z" |
| created_before | string | 否 | 创建时间终点，ISO 8601 格式 |

**示例**
```
GET /api/v1/tasks?status=completed&search=Rick&created_after=2025-01-01T00:00:00Z&limit=20&offset=0
```

---

## 四、数据库修改

### 4.1 Database 类新增方法

#### `list_video_resources()`

```python
async def list_video_resources(
    self,
    search: Optional[str] = None,
    limit: int = 20,
    offset: int = 0
) -> tuple[list[dict], int]:
    """
    列出视频资源（支持搜索和分页）

    Args:
        search: 搜索关键词（匹配 video_id 或标题）
        limit: 每页数量
        offset: 偏移量

    Returns:
        (资源列表, 总数)

    资源字段：
        - video_id
        - video_info
        - files_count: {audio: int, transcript: int}
        - upload_source: "auto" | "manual"
        - created_at
    """
```

**SQL 查询逻辑**
```sql
SELECT
  vr.video_id,
  vr.video_info,
  vr.created_at,
  COUNT(CASE WHEN f.file_type = 'audio' THEN 1 END) as audio_count,
  COUNT(CASE WHEN f.file_type = 'transcript' THEN 1 END) as transcript_count,
  MAX(CASE WHEN f.upload_source = 'manual' THEN 1 ELSE 0 END) as is_manual
FROM video_resources vr
LEFT JOIN files f ON vr.video_id = f.video_id
WHERE
  (? IS NULL OR vr.video_id LIKE ? OR json_extract(vr.video_info, '$.title') LIKE ?)
GROUP BY vr.video_id
ORDER BY vr.created_at DESC
LIMIT ? OFFSET ?
```

---

#### `get_video_resource_detail()`

```python
async def get_video_resource_detail(
    self,
    video_id: str
) -> Optional[dict]:
    """
    获取视频资源详情（含文件和任务）

    Args:
        video_id: 视频 ID

    Returns:
        {
            video_id: str,
            video_info: dict,
            files: list[FileRecord],
            related_tasks: list[dict],
            created_at: str,
            updated_at: str
        }
        如果不存在返回 None
    """
```

**查询逻辑**
1. 查询 `video_resources` 表
2. 查询 `files` 表：`WHERE video_id = ?`
3. 查询 `tasks` 表：`WHERE video_id = ? ORDER BY created_at DESC LIMIT 10`

---

#### `delete_video_resource()`

```python
async def delete_video_resource(
    self,
    video_id: str
) -> dict[str, Any]:
    """
    删除视频资源（级联删除文件，保留任务）

    Args:
        video_id: 视频 ID

    Returns:
        {
            success: bool,
            deleted_files: int,
            file_paths: list[str]  # 用于物理删除
        }
    """
```

**事务操作**
```python
async with self.transaction():
    # 1. 查询所有关联文件（获取 filepath）
    files = await self.execute(
        "SELECT filepath FROM files WHERE video_id = ?", (video_id,)
    ).fetchall()

    # 2. 删除 files 表记录
    await self.execute("DELETE FROM files WHERE video_id = ?", (video_id,))

    # 3. 删除 video_resources 表记录
    result = await self.execute(
        "DELETE FROM video_resources WHERE video_id = ?", (video_id,)
    )

    # 4. 任务表不删除！保留审计日志

    return {
        "success": result.rowcount > 0,
        "deleted_files": len(files),
        "file_paths": [f["filepath"] for f in files]
    }
```

---

#### `list_tasks()` 增强

**修改现有方法，新增参数**
```python
async def list_tasks(
    self,
    status: Optional[TaskStatus] = None,
    search: Optional[str] = None,  # 新增
    created_after: Optional[datetime] = None,  # 新增
    created_before: Optional[datetime] = None,  # 新增
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Task], int]:
```

**SQL 查询逻辑**
```sql
SELECT * FROM tasks
WHERE 1=1
  AND (? IS NULL OR status = ?)
  AND (? IS NULL OR video_id LIKE ? OR json_extract(video_info, '$.title') LIKE ?)
  AND (? IS NULL OR created_at >= ?)
  AND (? IS NULL OR created_at <= ?)
ORDER BY created_at DESC
LIMIT ? OFFSET ?
```

---

## 五、前端实现

### 5.1 文件结构

```
src/static/admin/
├── index.html      # 主页面（Tab 导航结构）
├── app.js          # JavaScript 逻辑（模块化）
└── style.css       # 样式表
```

---

### 5.2 HTML 结构（`index.html`）

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>管理中心 - YouTube Audio API</title>
  <link rel="stylesheet" href="/admin/style.css">
</head>
<body>
  <div class="container">
    <!-- 顶部栏 -->
    <header class="header">
      <h1>YouTube Audio API 管理中心</h1>
      <div class="header-actions">
        <input id="api-key" type="password" placeholder="X-API-Key" />
        <button class="btn" onclick="saveApiKey()">保存</button>
        <button class="btn-secondary" onclick="clearApiKey()">清除</button>
        <button class="btn-secondary" onclick="refreshCurrentTab()">🔄 刷新</button>
      </div>
    </header>

    <!-- Tab 导航 -->
    <nav class="tabs">
      <button class="tab active" data-tab="queue">任务队列</button>
      <button class="tab" data-tab="history">任务历史</button>
      <button class="tab" data-tab="resources">视频资源</button>
      <button class="tab" data-tab="create">创建/上传</button>
    </nav>

    <!-- Tab 内容区 -->
    <div id="tab-queue" class="tab-content active">
      <!-- 任务队列 -->
      <section class="card">
        <h2>队列中的任务</h2>
        <div id="queue-pending" class="queue-list"></div>
      </section>
      <section class="card">
        <h2>下载中</h2>
        <div id="queue-downloading" class="queue-list"></div>
      </section>
    </div>

    <div id="tab-history" class="tab-content">
      <!-- 任务历史 -->
      <section class="card">
        <h2>筛选器</h2>
        <div class="filters">
          <select id="filter-status">
            <option value="">全部状态</option>
            <option value="completed">已完成</option>
            <option value="failed">失败</option>
            <option value="cancelled">已取消</option>
          </select>
          <input id="filter-search" type="text" placeholder="搜索 video_id 或标题" />
          <input id="filter-date-start" type="date" />
          <span>至</span>
          <input id="filter-date-end" type="date" />
          <button class="btn" onclick="applyHistoryFilters()">应用</button>
          <button class="btn-secondary" onclick="resetHistoryFilters()">重置</button>
        </div>
      </section>
      <section class="card">
        <div id="history-list"></div>
        <div class="pagination">
          <button class="btn-secondary" onclick="prevPageHistory()">上一页</button>
          <span id="history-page-info"></span>
          <button class="btn-secondary" onclick="nextPageHistory()">下一页</button>
        </div>
      </section>
    </div>

    <div id="tab-resources" class="tab-content">
      <!-- 视频资源 -->
      <section class="card">
        <h2>视频资源库</h2>
        <div class="search-bar">
          <input id="resource-search" type="text" placeholder="搜索 video_id 或标题" />
          <button class="btn" onclick="searchResources()">搜索</button>
        </div>
      </section>
      <section class="card">
        <div id="resource-list"></div>
        <div class="pagination">
          <button class="btn-secondary" onclick="prevPageResources()">上一页</button>
          <span id="resource-page-info"></span>
          <button class="btn-secondary" onclick="nextPageResources()">下一页</button>
        </div>
      </section>
    </div>

    <div id="tab-create" class="tab-content">
      <!-- 创建/上传 -->
      <section class="card">
        <h2>创建下载任务</h2>
        <form id="create-task-form" onsubmit="handleCreateTask(event)">
          <input name="video_url" type="text" placeholder="https://www.youtube.com/watch?v=..." required />
          <div class="form-row">
            <label>
              <input name="include_audio" type="checkbox" checked /> 下载音频
            </label>
            <label>
              <input name="include_transcript" type="checkbox" checked /> 获取字幕
            </label>
          </div>
          <div class="form-row">
            <label>优先级：</label>
            <select name="priority">
              <option value="normal">普通</option>
              <option value="urgent">紧急</option>
            </select>
          </div>
          <button class="btn" type="submit">创建任务</button>
        </form>
      </section>

      <section class="card">
        <h2>人工上传</h2>
        <form id="upload-form" onsubmit="handleUpload(event)">
          <input name="video_url" type="text" placeholder="https://www.youtube.com/watch?v=..." required />
          <details class="metadata-section">
            <summary>可选元数据</summary>
            <div class="grid">
              <input name="title" type="text" placeholder="标题" />
              <input name="author" type="text" placeholder="作者" />
              <input name="duration" type="number" placeholder="时长(秒)" />
              <input name="channel_id" type="text" placeholder="频道 ID" />
            </div>
            <textarea name="description" placeholder="描述"></textarea>
          </details>
          <input name="file" type="file" required />
          <button class="btn" type="submit">上传</button>
          <div id="upload-progress" class="progress" style="display:none;">
            <div class="progress-bar"></div>
            <span class="progress-text">上传中...</span>
          </div>
        </form>
      </section>
    </div>
  </div>

  <!-- 弹窗 -->
  <div id="modal" class="modal">
    <div class="modal-content">
      <span class="close" onclick="closeModal()">&times;</span>
      <div id="modal-body"></div>
    </div>
  </div>

  <!-- Toast -->
  <div id="toast" class="toast"></div>

  <script src="/admin/app.js"></script>
</body>
</html>
```

---

### 5.3 JavaScript 逻辑（`app.js`）

#### 全局状态

```javascript
// ============ 全局状态 ============
const state = {
  apiKey: localStorage.getItem('apiKey') || '',
  currentTab: 'queue',
  pagination: {
    history: { offset: 0, limit: 20, total: 0 },
    resources: { offset: 0, limit: 20, total: 0 },
  },
  filters: {
    history: {
      status: '',
      search: '',
      dateStart: '',
      dateEnd: ''
    },
    resources: {
      search: ''
    },
  }
};

// 初始化
document.addEventListener('DOMContentLoaded', () => {
  initTabs();
  loadApiKey();
  loadCurrentTab();
});
```

#### Tab 切换

```javascript
// ============ Tab 切换 ============
function initTabs() {
  document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
      const tabName = tab.dataset.tab;
      switchTab(tabName);
    });
  });
}

function switchTab(tabName) {
  // 更新 Tab 按钮状态
  document.querySelectorAll('.tab').forEach(tab => {
    tab.classList.toggle('active', tab.dataset.tab === tabName);
  });

  // 更新内容区显示
  document.querySelectorAll('.tab-content').forEach(content => {
    content.classList.toggle('active', content.id === `tab-${tabName}`);
  });

  state.currentTab = tabName;
  loadTabData(tabName);
}

function loadTabData(tabName) {
  switch(tabName) {
    case 'queue': loadQueue(); break;
    case 'history': loadHistory(); break;
    case 'resources': loadResources(); break;
    case 'create': /* 无需加载 */ break;
  }
}

function refreshCurrentTab() {
  loadTabData(state.currentTab);
}
```

#### API 请求封装

```javascript
// ============ API 请求封装 ============
async function apiRequest(url, options = {}) {
  const headers = {
    'X-API-Key': state.apiKey,
    'Content-Type': 'application/json',
    ...options.headers
  };

  try {
    const response = await fetch(url, { ...options, headers });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || `HTTP ${response.status}`);
    }

    return response.json();
  } catch (err) {
    showToast(`请求失败: ${err.message}`, 'error');
    throw err;
  }
}
```

#### Tab 1: 任务队列

```javascript
// ============ Tab 1: 任务队列 ============
async function loadQueue() {
  try {
    // 加载 pending 和 downloading 任务
    const [pending, downloading] = await Promise.all([
      apiRequest('/api/v1/tasks?status=pending&limit=100'),
      apiRequest('/api/v1/tasks?status=downloading')
    ]);

    renderQueuePending(pending.items);
    renderQueueDownloading(downloading.items);
  } catch (err) {
    console.error('Failed to load queue:', err);
  }
}

function renderQueuePending(tasks) {
  const container = document.getElementById('queue-pending');

  if (tasks.length === 0) {
    container.innerHTML = '<p class="empty">队列为空</p>';
    return;
  }

  container.innerHTML = tasks.map(task => `
    <div class="queue-item">
      <div class="queue-info">
        <span class="priority-badge ${task.priority}">${task.priority === 'urgent' ? '🔥 紧急' : '普通'}</span>
        <span class="queue-position">队列位置: ${task.position || '-'}</span>
        <span class="video-id">${task.video_id}</span>
        <span class="task-type">${getTaskTypeLabel(task.request)}</span>
      </div>
      <div class="queue-meta">
        <span>${task.video_info?.title || '-'}</span>
        <span>预计等待: ${task.estimated_wait ? formatDuration(task.estimated_wait) : '-'}</span>
      </div>
      <button class="btn-danger" onclick="cancelTask('${task.task_id}')">取消</button>
    </div>
  `).join('');
}

function renderQueueDownloading(tasks) {
  const container = document.getElementById('queue-downloading');

  if (tasks.length === 0) {
    container.innerHTML = '<p class="empty">暂无下载中的任务</p>';
    return;
  }

  container.innerHTML = tasks.map(task => `
    <div class="queue-item downloading">
      <div class="queue-info">
        <span class="status-badge downloading">⬇️ 下载中</span>
        <span class="video-id">${task.video_id}</span>
        <span class="task-type">${getTaskTypeLabel(task.request)}</span>
      </div>
      <div class="queue-meta">
        <span>${task.video_info?.title || '-'}</span>
      </div>
    </div>
  `).join('');
}

async function cancelTask(taskId) {
  if (!confirm('确定取消该任务？')) return;

  try {
    await apiRequest(`/api/v1/tasks/${taskId}`, { method: 'DELETE' });
    showToast('任务已取消');
    loadQueue();
  } catch (err) {
    console.error('Failed to cancel task:', err);
  }
}
```

#### Tab 2: 任务历史

```javascript
// ============ Tab 2: 任务历史 ============
async function loadHistory() {
  const { status, search, dateStart, dateEnd } = state.filters.history;
  const { offset, limit } = state.pagination.history;

  let url = `/api/v1/tasks?limit=${limit}&offset=${offset}`;
  if (status) url += `&status=${status}`;
  if (search) url += `&search=${encodeURIComponent(search)}`;
  if (dateStart) url += `&created_after=${dateStart}T00:00:00Z`;
  if (dateEnd) url += `&created_before=${dateEnd}T23:59:59Z`;

  try {
    const data = await apiRequest(url);
    state.pagination.history.total = data.total;
    renderHistory(data.items);
    updateHistoryPagination();
  } catch (err) {
    console.error('Failed to load history:', err);
  }
}

function renderHistory(tasks) {
  const container = document.getElementById('history-list');

  if (tasks.length === 0) {
    container.innerHTML = '<p class="empty">暂无任务记录</p>';
    return;
  }

  container.innerHTML = `
    <table class="task-table">
      <thead>
        <tr>
          <th>Video ID</th>
          <th>标题</th>
          <th>状态</th>
          <th>类型</th>
          <th>缓存</th>
          <th>创建时间</th>
          <th>完成时间</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        ${tasks.map(task => `
          <tr>
            <td>${task.video_id}</td>
            <td>${task.video_info?.title || '-'}</td>
            <td><span class="status-badge ${task.status}">${getStatusLabel(task.status)}</span></td>
            <td>${getTaskTypeLabel(task.request)}</td>
            <td>${task.cache_hit ? '✓' : '-'}</td>
            <td>${formatDateTime(task.created_at)}</td>
            <td>${task.completed_at ? formatDateTime(task.completed_at) : '-'}</td>
            <td><button class="btn-secondary" onclick="showTaskDetail('${task.task_id}')">详情</button></td>
          </tr>
        `).join('')}
      </tbody>
    </table>
  `;
}

function applyHistoryFilters() {
  state.filters.history = {
    status: document.getElementById('filter-status').value,
    search: document.getElementById('filter-search').value,
    dateStart: document.getElementById('filter-date-start').value,
    dateEnd: document.getElementById('filter-date-end').value,
  };
  state.pagination.history.offset = 0;
  loadHistory();
}

function resetHistoryFilters() {
  state.filters.history = { status: '', search: '', dateStart: '', dateEnd: '' };
  document.getElementById('filter-status').value = '';
  document.getElementById('filter-search').value = '';
  document.getElementById('filter-date-start').value = '';
  document.getElementById('filter-date-end').value = '';
  state.pagination.history.offset = 0;
  loadHistory();
}

function prevPageHistory() {
  if (state.pagination.history.offset > 0) {
    state.pagination.history.offset -= state.pagination.history.limit;
    loadHistory();
  }
}

function nextPageHistory() {
  const { offset, limit, total } = state.pagination.history;
  if (offset + limit < total) {
    state.pagination.history.offset += limit;
    loadHistory();
  }
}

function updateHistoryPagination() {
  const { offset, limit, total } = state.pagination.history;
  const current = Math.floor(offset / limit) + 1;
  const totalPages = Math.ceil(total / limit);
  document.getElementById('history-page-info').textContent = `第 ${current} / ${totalPages} 页（共 ${total} 条）`;
}

async function showTaskDetail(taskId) {
  try {
    const task = await apiRequest(`/api/v1/tasks/${taskId}`);
    const html = `
      <h2>任务详情</h2>
      <div class="detail-section">
        <h3>基本信息</h3>
        <p><strong>Task ID:</strong> ${task.task_id}</p>
        <p><strong>Video ID:</strong> ${task.video_id}</p>
        <p><strong>状态:</strong> ${getStatusLabel(task.status)}</p>
        <p><strong>优先级:</strong> ${task.priority}</p>
        <p><strong>创建时间:</strong> ${formatDateTime(task.created_at)}</p>
        <p><strong>完成时间:</strong> ${task.completed_at ? formatDateTime(task.completed_at) : '-'}</p>
      </div>

      <div class="detail-section">
        <h3>视频信息</h3>
        ${task.video_info ? `
          <p><strong>标题:</strong> ${task.video_info.title}</p>
          <p><strong>作者:</strong> ${task.video_info.author}</p>
          <p><strong>时长:</strong> ${task.video_info.duration}秒</p>
        ` : '<p>无信息</p>'}
      </div>

      ${task.files ? `
        <div class="detail-section">
          <h3>文件</h3>
          ${task.files.audio ? `<p><a href="${task.files.audio.url}" target="_blank">音频文件 (${formatFileSize(task.files.audio.size)})</a></p>` : ''}
          ${task.files.transcript ? `<p><a href="${task.files.transcript.url}" target="_blank">字幕文件 (${formatFileSize(task.files.transcript.size)})</a></p>` : ''}
        </div>
      ` : ''}

      ${task.error ? `
        <div class="detail-section error">
          <h3>错误信息</h3>
          <p><strong>错误码:</strong> ${task.error.code}</p>
          <p><strong>错误信息:</strong> ${task.error.message}</p>
        </div>
      ` : ''}
    `;
    showModal(html);
  } catch (err) {
    console.error('Failed to load task detail:', err);
  }
}
```

#### Tab 3: 视频资源

```javascript
// ============ Tab 3: 视频资源 ============
async function loadResources() {
  const { search } = state.filters.resources;
  const { offset, limit } = state.pagination.resources;

  let url = `/api/v1/video-resources?limit=${limit}&offset=${offset}`;
  if (search) url += `&search=${encodeURIComponent(search)}`;

  try {
    const data = await apiRequest(url);
    state.pagination.resources.total = data.total;
    renderResources(data.items);
    updateResourcesPagination();
  } catch (err) {
    console.error('Failed to load resources:', err);
  }
}

function searchResources() {
  state.filters.resources.search = document.getElementById('resource-search').value;
  state.pagination.resources.offset = 0;
  loadResources();
}

function renderResources(resources) {
  const container = document.getElementById('resource-list');

  if (resources.length === 0) {
    container.innerHTML = '<p class="empty">暂无视频资源</p>';
    return;
  }

  container.innerHTML = `
    <div class="resource-grid">
      ${resources.map(resource => `
        <div class="resource-card">
          <img src="${resource.video_info?.thumbnail || '/placeholder.jpg'}" alt="缩略图" />
          <div class="resource-info">
            <h3>${resource.video_info?.title || resource.video_id}</h3>
            <p class="author">${resource.video_info?.author || '-'}</p>
            <div class="resource-meta">
              <span class="badge source-${resource.upload_source}">${resource.upload_source === 'manual' ? '人工上传' : '自动下载'}</span>
              <span>音频: ${resource.files_count.audio}</span>
              <span>字幕: ${resource.files_count.transcript}</span>
            </div>
            <p class="date">${formatDateTime(resource.created_at)}</p>
            <div class="resource-actions">
              <button class="btn-secondary" onclick="showResourceDetail('${resource.video_id}')">详情</button>
              <button class="btn-danger" onclick="deleteResource('${resource.video_id}')">删除</button>
            </div>
          </div>
        </div>
      `).join('')}
    </div>
  `;
}

function prevPageResources() {
  if (state.pagination.resources.offset > 0) {
    state.pagination.resources.offset -= state.pagination.resources.limit;
    loadResources();
  }
}

function nextPageResources() {
  const { offset, limit, total } = state.pagination.resources;
  if (offset + limit < total) {
    state.pagination.resources.offset += limit;
    loadResources();
  }
}

function updateResourcesPagination() {
  const { offset, limit, total } = state.pagination.resources;
  const current = Math.floor(offset / limit) + 1;
  const totalPages = Math.ceil(total / limit);
  document.getElementById('resource-page-info').textContent = `第 ${current} / ${totalPages} 页（共 ${total} 条）`;
}

async function showResourceDetail(videoId) {
  try {
    const resource = await apiRequest(`/api/v1/video-resources/${videoId}`);
    const html = `
      <h2>视频资源详情</h2>
      <div class="detail-section">
        <h3>基本信息</h3>
        <p><strong>Video ID:</strong> ${resource.video_id}</p>
        <p><strong>标题:</strong> ${resource.video_info?.title || '-'}</p>
        <p><strong>作者:</strong> ${resource.video_info?.author || '-'}</p>
        <p><strong>时长:</strong> ${resource.video_info?.duration || '-'}秒</p>
        <p><strong>创建时间:</strong> ${formatDateTime(resource.created_at)}</p>
      </div>

      <div class="detail-section">
        <h3>文件列表</h3>
        ${resource.files.length > 0 ? resource.files.map(file => `
          <div class="file-item">
            <p><strong>类型:</strong> ${file.file_type}</p>
            <p><strong>格式:</strong> ${file.format}</p>
            <p><strong>大小:</strong> ${formatFileSize(file.size)}</p>
            <p><strong>来源:</strong> ${file.upload_source === 'manual' ? '人工上传' : '自动下载'}</p>
            <p><a href="${file.url}" target="_blank">下载文件</a></p>
          </div>
        `).join('') : '<p>暂无文件</p>'}
      </div>

      <div class="detail-section">
        <h3>相关任务 (最近10个)</h3>
        ${resource.related_tasks.length > 0 ? `
          <ul>
            ${resource.related_tasks.map(task => `
              <li>${task.task_id} - ${getStatusLabel(task.status)} (${formatDateTime(task.created_at)})</li>
            `).join('')}
          </ul>
        ` : '<p>暂无相关任务</p>'}
      </div>
    `;
    showModal(html);
  } catch (err) {
    console.error('Failed to load resource detail:', err);
  }
}

async function deleteResource(videoId) {
  if (!confirm('确定删除该视频资源及所有文件？任务记录将保留。')) return;

  try {
    const result = await apiRequest(`/api/v1/video-resources/${videoId}`, { method: 'DELETE' });
    showToast(`删除成功，已删除 ${result.deleted_files} 个文件`);
    loadResources();
  } catch (err) {
    console.error('Failed to delete resource:', err);
  }
}
```

#### Tab 4: 创建/上传

```javascript
// ============ Tab 4: 创建/上传 ============
async function handleCreateTask(event) {
  event.preventDefault();
  const form = event.target;
  const formData = new FormData(form);

  const data = {
    video_url: formData.get('video_url'),
    priority: formData.get('priority'),
    include_audio: formData.get('include_audio') === 'on',
    include_transcript: formData.get('include_transcript') === 'on',
  };

  try {
    const response = await apiRequest('/api/v1/tasks', {
      method: 'POST',
      body: JSON.stringify(data)
    });

    showToast('任务创建成功');
    form.reset();
    switchTab('queue');
  } catch (err) {
    console.error('Failed to create task:', err);
  }
}

async function handleUpload(event) {
  event.preventDefault();
  const form = event.target;
  const formData = new FormData(form);

  // 显示进度条
  const progress = document.getElementById('upload-progress');
  progress.style.display = 'block';

  try {
    const response = await fetch('/api/v1/manual-upload', {
      method: 'POST',
      headers: {
        'X-API-Key': state.apiKey
      },
      body: formData
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || `HTTP ${response.status}`);
    }

    const result = await response.json();
    showToast('上传成功');
    form.reset();
    progress.style.display = 'none';
  } catch (err) {
    showToast(`上传失败: ${err.message}`, 'error');
    progress.style.display = 'none';
  }
}
```

#### 工具函数

```javascript
// ============ 工具函数 ============
function saveApiKey() {
  const apiKey = document.getElementById('api-key').value;
  if (apiKey) {
    localStorage.setItem('apiKey', apiKey);
    state.apiKey = apiKey;
    showToast('API Key 已保存');
  }
}

function clearApiKey() {
  localStorage.removeItem('apiKey');
  state.apiKey = '';
  document.getElementById('api-key').value = '';
  showToast('API Key 已清除');
}

function loadApiKey() {
  const apiKey = localStorage.getItem('apiKey');
  if (apiKey) {
    document.getElementById('api-key').value = apiKey;
    state.apiKey = apiKey;
  }
}

function loadCurrentTab() {
  loadTabData(state.currentTab);
}

function showModal(content) {
  const modal = document.getElementById('modal');
  document.getElementById('modal-body').innerHTML = content;
  modal.style.display = 'flex';
}

function closeModal() {
  document.getElementById('modal').style.display = 'none';
}

function showToast(message, type = 'success') {
  const toast = document.getElementById('toast');
  toast.textContent = message;
  toast.className = `toast ${type} show`;
  setTimeout(() => {
    toast.classList.remove('show');
  }, 3000);
}

function getStatusLabel(status) {
  const labels = {
    pending: '等待中',
    downloading: '下载中',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消'
  };
  return labels[status] || status;
}

function getTaskTypeLabel(request) {
  if (request.include_audio && request.include_transcript) return '音频+字幕';
  if (request.include_audio) return '仅音频';
  if (request.include_transcript) return '仅字幕';
  return '-';
}

function formatDateTime(dateStr) {
  if (!dateStr) return '-';
  const date = new Date(dateStr);
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  });
}

function formatFileSize(bytes) {
  if (!bytes) return '-';
  const mb = bytes / (1024 * 1024);
  return `${mb.toFixed(2)} MB`;
}

function formatDuration(seconds) {
  if (!seconds) return '-';
  if (seconds < 60) return `${seconds}秒`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}分钟`;
}
```

---

### 5.4 CSS 样式（`style.css`）

重点样式：

```css
/* Tab 导航 */
.tabs {
  display: flex;
  border-bottom: 2px solid #ddd;
  margin-bottom: 20px;
}

.tab {
  padding: 12px 24px;
  background: none;
  border: none;
  border-bottom: 3px solid transparent;
  cursor: pointer;
  font-size: 16px;
  transition: all 0.3s;
}

.tab:hover {
  background: #f5f5f5;
}

.tab.active {
  border-bottom-color: #2196f3;
  color: #2196f3;
  font-weight: bold;
}

/* Tab 内容 */
.tab-content {
  display: none;
}

.tab-content.active {
  display: block;
}

/* 优先级标签 */
.priority-badge {
  display: inline-block;
  padding: 4px 12px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: bold;
}

.priority-badge.urgent {
  background: #ff4444;
  color: white;
}

.priority-badge.normal {
  background: #888;
  color: white;
}

/* 状态徽章 */
.status-badge {
  display: inline-block;
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 12px;
}

.status-badge.pending { background: #ffa500; color: white; }
.status-badge.downloading { background: #00bfff; color: white; }
.status-badge.completed { background: #32cd32; color: white; }
.status-badge.failed { background: #ff4444; color: white; }
.status-badge.cancelled { background: #888; color: white; }

/* 来源标签 */
.badge.source-manual { background: #9c27b0; color: white; }
.badge.source-auto { background: #2196f3; color: white; }

/* 队列项 */
.queue-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px;
  border: 1px solid #ddd;
  border-radius: 4px;
  margin-bottom: 8px;
}

.queue-item.downloading {
  background: #e3f2fd;
  border-color: #2196f3;
}

/* 资源网格 */
.resource-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 20px;
}

.resource-card {
  border: 1px solid #ddd;
  border-radius: 8px;
  overflow: hidden;
}

.resource-card img {
  width: 100%;
  height: 180px;
  object-fit: cover;
}

.resource-info {
  padding: 12px;
}

/* 弹窗 */
.modal {
  display: none;
  position: fixed;
  z-index: 1000;
  left: 0;
  top: 0;
  width: 100%;
  height: 100%;
  background: rgba(0, 0, 0, 0.5);
  align-items: center;
  justify-content: center;
}

.modal-content {
  background: white;
  padding: 20px;
  border-radius: 8px;
  max-width: 800px;
  max-height: 80vh;
  overflow-y: auto;
  position: relative;
}

.close {
  position: absolute;
  top: 10px;
  right: 20px;
  font-size: 28px;
  font-weight: bold;
  cursor: pointer;
}

/* Toast */
.toast {
  position: fixed;
  bottom: 20px;
  right: 20px;
  padding: 12px 24px;
  border-radius: 4px;
  background: #333;
  color: white;
  opacity: 0;
  transition: opacity 0.3s;
  z-index: 2000;
}

.toast.show {
  opacity: 1;
}

.toast.error {
  background: #ff4444;
}

.toast.success {
  background: #32cd32;
}
```

---

## 六、实现步骤

### 阶段 1: 后端 API（1-2天）

1. **Database 层**
   - [ ] 实现 `list_video_resources()`
   - [ ] 实现 `get_video_resource_detail()`
   - [ ] 实现 `delete_video_resource()`
   - [ ] 增强 `list_tasks()`（添加 search、created_after、created_before 参数）

2. **API 路由**
   - [ ] 创建 `src/api/video_resource_routes.py`
   - [ ] 实现 3 个视频资源接口
   - [ ] 修改 `src/api/routes.py`，增强任务列表接口
   - [ ] 在 `src/main.py` 注册新路由

3. **测试**
   - [ ] 使用 curl 或 Postman 测试所有新增接口
   - [ ] 验证删除操作（级联删除文件、保留任务）
   - [ ] 验证搜索和筛选功能

### 阶段 2: 前端页面（2-3天）

4. **HTML 结构**
   - [ ] 重构 `src/static/admin/index.html`
   - [ ] 实现 Tab 导航结构
   - [ ] 实现 4 个 Tab 的 HTML 布局

5. **JavaScript 逻辑**
   - [ ] 实现 Tab 切换逻辑
   - [ ] 实现任务队列加载和渲染
   - [ ] 实现任务历史加载、筛选、分页
   - [ ] 实现视频资源加载、搜索、分页
   - [ ] 实现详情弹窗
   - [ ] 实现创建任务和上传功能（复用现有逻辑）

6. **CSS 样式**
   - [ ] 更新 `src/static/admin/style.css`
   - [ ] 实现 Tab 样式
   - [ ] 实现标签、徽章样式
   - [ ] 实现响应式布局

### 阶段 3: 测试和完善（1天）

7. **功能测试**
   - [ ] 测试所有 Tab 的数据加载
   - [ ] 测试筛选、搜索、分页
   - [ ] 测试删除操作
   - [ ] 测试弹窗和 Toast

8. **优化**
   - [ ] 错误处理完善
   - [ ] 空状态提示
   - [ ] 性能优化（防抖、懒加载）

---

## 七、测试要点

### 7.1 后端测试

#### 视频资源列表
```bash
# 列出所有资源
curl -H "X-API-Key: your-key" "http://localhost:8000/api/v1/video-resources?limit=20&offset=0"

# 搜索资源
curl -H "X-API-Key: your-key" "http://localhost:8000/api/v1/video-resources?search=Rick"
```

#### 视频资源详情
```bash
curl -H "X-API-Key: your-key" "http://localhost:8000/api/v1/video-resources/dQw4w9WgXcQ"
```

#### 删除视频资源
```bash
curl -X DELETE -H "X-API-Key: your-key" "http://localhost:8000/api/v1/video-resources/dQw4w9WgXcQ"

# 验证：
# 1. files 表中相关记录已删除
# 2. video_resources 表中记录已删除
# 3. 物理文件已删除
# 4. tasks 表中记录仍存在
```

#### 任务历史筛选
```bash
# 按日期筛选
curl -H "X-API-Key: your-key" "http://localhost:8000/api/v1/tasks?created_after=2025-01-01T00:00:00Z"

# 按关键词搜索
curl -H "X-API-Key: your-key" "http://localhost:8000/api/v1/tasks?search=Rick"

# 组合筛选
curl -H "X-API-Key: your-key" "http://localhost:8000/api/v1/tasks?status=completed&search=Rick&created_after=2025-01-01T00:00:00Z"
```

### 7.2 前端测试

#### 功能测试清单
- [ ] API Key 保存和清除
- [ ] Tab 切换正常
- [ ] 任务队列显示正常（pending 和 downloading）
- [ ] 取消任务功能正常
- [ ] 任务历史筛选器正常（状态、日期、搜索）
- [ ] 任务历史分页正常
- [ ] 任务详情弹窗正常
- [ ] 视频资源搜索正常
- [ ] 视频资源分页正常
- [ ] 视频资源详情弹窗正常
- [ ] 删除视频资源功能正常
- [ ] 创建任务功能正常
- [ ] 人工上传功能正常
- [ ] Toast 提示正常
- [ ] 错误处理正常

#### 边界测试
- [ ] 空状态显示（无数据时）
- [ ] 大量数据加载（100+ 任务）
- [ ] 搜索无结果
- [ ] 网络错误处理
- [ ] API Key 缺失提示

---

## 八、注意事项

### 8.1 删除操作的审计日志

删除视频资源时，任务记录会保留，但可以在任务详情中标注"资源已删除"：

```python
# 在 get_task() 中检查视频资源是否存在
task = await db.get_task(task_id)
video_resource = await db.get_video_resource(task.video_id)

if not video_resource:
    task.message = "注意：该视频资源已被删除"
```

### 8.2 性能优化

- **搜索防抖**：搜索框输入时使用 300ms 防抖，避免频繁请求
- **图片懒加载**：缩略图使用 `loading="lazy"` 属性
- **分页大小**：默认 20 条/页，最大 100 条/页

### 8.3 安全性

- **API Key 保护**：所有 API 请求都需要 X-API-Key
- **删除确认**：删除操作需要用户二次确认
- **XSS 防护**：所有用户输入的内容都需要转义

### 8.4 可扩展性

未来可能的扩展：
- 批量操作（批量删除、批量取消）
- 导出功能（导出任务历史 CSV）
- 统计图表（任务成功率、下载量统计）
- WebSocket 实时更新（任务状态变化推送）

---

## 九、总结

这个方案的核心思路：

1. **简单实用**：不追求复杂交互，满足基本的查看和管理需求
2. **模块化设计**：前后端分离，代码结构清晰，易于维护
3. **数据完整性**：删除资源时保留任务记录，保证审计日志完整
4. **技术栈简单**：原生 HTML + CSS + JavaScript，无依赖，降低维护成本

实现后，Web UI 将成为一个完善的辅助管理工具，支持：
- ✅ 实时任务队列监控
- ✅ 任务历史查询（支持筛选、搜索）
- ✅ 视频资源管理（查看、删除）
- ✅ 创建任务和文件上传

代码量可控，维护成本低，满足临时管理和查看的需求。
