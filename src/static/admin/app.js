// ==================== 全局状态 ====================
const state = {
  apiKey: localStorage.getItem('apiKey') || '',
  currentTab: 'queue',
  pagination: {
    history: { offset: 0, limit: 20, total: 0 },
    resources: { offset: 0, limit: 20, total: 0 },
  },
  filters: {
    history: { status: '', search: '', dateStart: '', dateEnd: '' },
    resources: { search: '' },
  },
  searchDebounceTimer: null,
};

// ==================== 初始化 ====================
window.addEventListener('DOMContentLoaded', () => {
  if (state.apiKey) {
    document.getElementById('api-key').value = state.apiKey;
  }
  loadTabData(state.currentTab);
});

// ==================== API 请求封装 ====================
async function apiRequest(url, options = {}) {
  const headers = {
    'X-API-Key': state.apiKey,
    ...(options.headers || {}),
  };

  if (options.body && !(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
  }

  const response = await fetch(url, {
    ...options,
    headers,
  });

  if (response.status === 401 || response.status === 403) {
    showToast('认证失败，请检查 API Key', 'error');
    throw new Error('Unauthorized');
  }

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
    throw new Error(error.detail || `HTTP ${response.status}`);
  }

  return response.json();
}

// ==================== API Key 管理 ====================
function saveApiKey() {
  const apiKey = document.getElementById('api-key').value.trim();
  if (!apiKey) {
    showToast('请输入 API Key', 'warning');
    return;
  }
  state.apiKey = apiKey;
  localStorage.setItem('apiKey', apiKey);
  showToast('API Key 已保存', 'success');
  refreshCurrentTab();
}

function clearApiKey() {
  state.apiKey = '';
  localStorage.removeItem('apiKey');
  document.getElementById('api-key').value = '';
  showToast('API Key 已清除', 'info');
}

function checkApiKey() {
  if (!state.apiKey) {
    showToast('请先设置 API Key', 'warning');
    return false;
  }
  return true;
}

// ==================== Tab 管理 ====================
function switchTab(tabName) {
  state.currentTab = tabName;

  // 更新 Tab 按钮状态
  document.querySelectorAll('.tab').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === tabName);
  });

  // 更新内容区显示
  document.querySelectorAll('.tab-content').forEach(content => {
    content.classList.toggle('active', content.id === `tab-${tabName}`);
  });

  // 加载对应数据
  loadTabData(tabName);
}

function refreshCurrentTab() {
  loadTabData(state.currentTab);
}

function loadTabData(tabName) {
  if (!checkApiKey()) return;

  switch (tabName) {
    case 'queue':
      loadQueue();
      break;
    case 'history':
      loadHistory();
      break;
    case 'resources':
      loadResources();
      break;
    case 'create':
      // 创建/上传 Tab 不需要加载数据
      break;
    case 'settings':
      loadCookie();
      break;
  }
}

// ==================== 任务队列 ====================
async function loadQueue() {
  try {
    const data = await apiRequest('/api/v1/tasks?limit=100');
    const downloading = data.tasks.filter(t => t.status === 'downloading');
    const pending = data.tasks.filter(t => t.status === 'pending');

    renderDownloadingTasks(downloading);
    renderPendingTasks(pending);
  } catch (error) {
    console.error('Failed to load queue:', error);
    showToast('加载任务队列失败: ' + error.message, 'error');
  }
}

function renderDownloadingTasks(tasks) {
  const container = document.getElementById('downloading-tasks');
  if (tasks.length === 0) {
    container.innerHTML = '<p class="empty">暂无正在下载的任务</p>';
    return;
  }

  container.innerHTML = tasks.map(task => `
    <div class="task-item downloading">
      <div class="task-header">
        <span class="badge priority-${task.priority}">${task.priority}</span>
        <strong>${escapeHtml(task.video_info?.title || task.video_id)}</strong>
      </div>
      <div class="task-meta">
        <span>Video ID: ${task.video_id}</span>
        <span>类型: ${getTaskType(task)}</span>
        <span>开始时间: ${formatDate(task.started_at)}</span>
      </div>
    </div>
  `).join('');
}

function renderPendingTasks(tasks) {
  const container = document.getElementById('pending-tasks');
  if (tasks.length === 0) {
    container.innerHTML = '<p class="empty">队列为空</p>';
    return;
  }

  container.innerHTML = tasks.map((task, index) => `
    <div class="task-item">
      <div class="task-header">
        <span class="badge queue-position">队列 #${index + 1}</span>
        <span class="badge priority-${task.priority}">${task.priority}</span>
        <strong>${escapeHtml(task.video_info?.title || task.video_id)}</strong>
        <button class="btn-danger-sm" onclick="cancelTask('${task.task_id}')">取消</button>
      </div>
      <div class="task-meta">
        <span>Video ID: ${task.video_id}</span>
        <span>类型: ${getTaskType(task)}</span>
        <span>创建时间: ${formatDate(task.created_at)}</span>
      </div>
    </div>
  `).join('');
}

async function cancelTask(taskId) {
  if (!confirm('确定要取消此任务吗？')) return;

  try {
    await apiRequest(`/api/v1/tasks/${taskId}`, { method: 'DELETE' });
    showToast('任务已取消', 'success');
    loadQueue();
  } catch (error) {
    showToast('取消任务失败: ' + error.message, 'error');
  }
}

// ==================== 任务历史 ====================
async function loadHistory() {
  const { offset, limit } = state.pagination.history;
  const { status, search, dateStart, dateEnd } = state.filters.history;

  const params = new URLSearchParams({
    limit: limit.toString(),
    offset: offset.toString(),
  });

  if (status) params.append('status', status);
  if (search) params.append('search', search);
  if (dateStart) params.append('created_after', new Date(dateStart).toISOString());
  if (dateEnd) params.append('created_before', new Date(dateEnd + 'T23:59:59').toISOString());

  try {
    const data = await apiRequest(`/api/v1/tasks?${params}`);
    state.pagination.history.total = data.total;
    renderHistoryTable(data.tasks);
    updateHistoryPagination();
  } catch (error) {
    console.error('Failed to load history:', error);
    showToast('加载任务历史失败: ' + error.message, 'error');
  }
}

function renderHistoryTable(tasks) {
  const tbody = document.getElementById('history-tbody');
  if (tasks.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty">暂无记录</td></tr>';
    return;
  }

  tbody.innerHTML = tasks.map(task => `
    <tr onclick="showTaskDetail('${task.task_id}')" style="cursor:pointer;">
      <td>${task.video_id}</td>
      <td>${escapeHtml(task.video_info?.title || '-')}</td>
      <td><span class="badge status-${task.status}">${task.status}</span></td>
      <td>${getTaskType(task)}</td>
      <td>${getCacheInfo(task)}</td>
      <td>${formatDate(task.created_at)}</td>
      <td>${formatDate(task.completed_at)}</td>
    </tr>
  `).join('');
}

function updateHistoryPagination() {
  const { offset, limit, total } = state.pagination.history;
  const currentPage = Math.floor(offset / limit) + 1;
  const totalPages = Math.ceil(total / limit);
  document.getElementById('history-page-info').textContent = `第 ${currentPage} 页，共 ${totalPages} 页（${total} 条）`;
}

function applyHistoryFilters() {
  state.filters.history.status = document.getElementById('status-filter').value;
  state.filters.history.search = document.getElementById('search-filter').value.trim();
  state.filters.history.dateStart = document.getElementById('date-start').value;
  state.filters.history.dateEnd = document.getElementById('date-end').value;
  state.pagination.history.offset = 0;
  loadHistory();
}

function debounceSearch() {
  clearTimeout(state.searchDebounceTimer);
  state.searchDebounceTimer = setTimeout(applyHistoryFilters, 300);
}

function historyPrevPage() {
  const { offset, limit } = state.pagination.history;
  if (offset > 0) {
    state.pagination.history.offset = Math.max(0, offset - limit);
    loadHistory();
  }
}

function historyNextPage() {
  const { offset, limit, total } = state.pagination.history;
  if (offset + limit < total) {
    state.pagination.history.offset = offset + limit;
    loadHistory();
  }
}

async function showTaskDetail(taskId) {
  try {
    const task = await apiRequest(`/api/v1/tasks/${taskId}`);
    const html = `
      <h2>任务详情</h2>
      <div class="detail-grid">
        <div><strong>任务 ID:</strong> ${task.task_id}</div>
        <div><strong>Video ID:</strong> ${task.video_id}</div>
        <div><strong>状态:</strong> <span class="badge status-${task.status}">${task.status}</span></div>
        <div><strong>优先级:</strong> ${task.priority}</div>
        <div><strong>标题:</strong> ${escapeHtml(task.video_info?.title || '-')}</div>
        <div><strong>作者:</strong> ${escapeHtml(task.video_info?.author || '-')}</div>
        <div><strong>时长:</strong> ${formatDuration(task.video_info?.duration)}</div>
        <div><strong>请求类型:</strong> ${getTaskType(task)}</div>
        <div><strong>缓存命中:</strong> ${getCacheInfo(task)}</div>
        ${task.error_message ? `<div class="error-message"><strong>错误:</strong> ${escapeHtml(task.error_message)}</div>` : ''}
        <div><strong>创建时间:</strong> ${formatDate(task.created_at)}</div>
        <div><strong>开始时间:</strong> ${formatDate(task.started_at)}</div>
        <div><strong>完成时间:</strong> ${formatDate(task.completed_at)}</div>
      </div>
      ${task.files ? `
        <h3>文件列表</h3>
        <ul>
          ${task.files.audio ? `<li>音频: ${task.files.audio.filename} (${formatBytes(task.files.audio.size)})</li>` : ''}
          ${task.files.transcript ? `<li>字幕: ${task.files.transcript.filename} (${formatBytes(task.files.transcript.size)})</li>` : ''}
        </ul>
      ` : ''}
    `;
    showModal(html);
  } catch (error) {
    showToast('加载任务详情失败: ' + error.message, 'error');
  }
}

// ==================== 视频资源 ====================
async function loadResources() {
  const { offset, limit } = state.pagination.resources;
  const { search } = state.filters.resources;

  const params = new URLSearchParams({
    limit: limit.toString(),
    offset: offset.toString(),
  });

  if (search) params.append('search', search);

  try {
    const data = await apiRequest(`/api/v1/video-resources?${params}`);
    state.pagination.resources.total = data.total;
    renderResourceGrid(data.resources);
    updateResourcePagination();
  } catch (error) {
    console.error('Failed to load resources:', error);
    showToast('加载视频资源失败: ' + error.message, 'error');
  }
}

function renderResourceGrid(resources) {
  const grid = document.getElementById('resource-grid');
  if (resources.length === 0) {
    grid.innerHTML = '<p class="empty">暂无视频资源</p>';
    return;
  }

  grid.innerHTML = resources.map(res => {
    const title = res.video_info?.title || res.video_id;
    const author = res.video_info?.author || '未知';
    const thumbnail = res.video_info?.thumbnail || `https://i.ytimg.com/vi/${res.video_id}/default.jpg`;

    return `
      <div class="resource-card">
        <img src="${thumbnail}" alt="${escapeHtml(title)}" loading="lazy" onerror="this.src='data:image/svg+xml,%3Csvg xmlns=\\'http://www.w3.org/2000/svg\\' width=\\'120\\' height=\\'90\\'%3E%3Crect fill=\\'%23ddd\\' width=\\'120\\' height=\\'90\\'/%3E%3C/svg%3E'" />
        <div class="resource-info">
          <h3>${escapeHtml(title)}</h3>
          <p>${escapeHtml(author)}</p>
          <div class="resource-meta">
            <span class="badge source-${res.upload_source}">${res.upload_source}</span>
            <span>${res.audio_count} 音频</span>
            <span>${res.transcript_count} 字幕</span>
          </div>
          <div class="resource-actions">
            <button class="btn-sm" onclick="showResourceDetail('${res.video_id}')">详情</button>
            <button class="btn-danger-sm" onclick="deleteResource('${res.video_id}')">删除</button>
          </div>
        </div>
      </div>
    `;
  }).join('');
}

function updateResourcePagination() {
  const { offset, limit, total } = state.pagination.resources;
  const currentPage = Math.floor(offset / limit) + 1;
  const totalPages = Math.ceil(total / limit);
  document.getElementById('resource-page-info').textContent = `第 ${currentPage} 页，共 ${totalPages} 页（${total} 条）`;
}

function debounceResourceSearch() {
  clearTimeout(state.searchDebounceTimer);
  state.searchDebounceTimer = setTimeout(() => {
    state.filters.resources.search = document.getElementById('resource-search').value.trim();
    state.pagination.resources.offset = 0;
    loadResources();
  }, 300);
}

function resourcePrevPage() {
  const { offset, limit } = state.pagination.resources;
  if (offset > 0) {
    state.pagination.resources.offset = Math.max(0, offset - limit);
    loadResources();
  }
}

function resourceNextPage() {
  const { offset, limit, total } = state.pagination.resources;
  if (offset + limit < total) {
    state.pagination.resources.offset = offset + limit;
    loadResources();
  }
}

async function showResourceDetail(videoId) {
  try {
    const resource = await apiRequest(`/api/v1/video-resources/${videoId}`);
    const html = `
      <h2>视频资源详情</h2>
      <div class="detail-grid">
        <div><strong>Video ID:</strong> ${resource.video_id}</div>
        <div><strong>标题:</strong> ${escapeHtml(resource.video_info?.title || '-')}</div>
        <div><strong>作者:</strong> ${escapeHtml(resource.video_info?.author || '-')}</div>
        <div><strong>时长:</strong> ${formatDuration(resource.video_info?.duration)}</div>
        <div><strong>有原生字幕:</strong> ${resource.has_native_transcript ? '是' : '否'}</div>
        <div><strong>创建时间:</strong> ${formatDate(resource.created_at)}</div>
        <div><strong>更新时间:</strong> ${formatDate(resource.updated_at)}</div>
      </div>
      <h3>文件列表 (${resource.files.length})</h3>
      <table class="modal-table">
        <thead>
          <tr>
            <th>类型</th>
            <th>文件名</th>
            <th>大小</th>
            <th>格式</th>
            <th>来源</th>
            <th>创建时间</th>
          </tr>
        </thead>
        <tbody>
          ${resource.files.map(f => `
            <tr>
              <td>${f.file_type}</td>
              <td>${escapeHtml(f.filename)}</td>
              <td>${formatBytes(f.size)}</td>
              <td>${f.format || '-'}</td>
              <td><span class="badge source-${f.upload_source}">${f.upload_source}</span></td>
              <td>${formatDate(f.created_at)}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
      <h3>最近任务 (${resource.recent_tasks.length})</h3>
      <table class="modal-table">
        <thead>
          <tr>
            <th>任务 ID</th>
            <th>状态</th>
            <th>缓存命中</th>
            <th>创建时间</th>
          </tr>
        </thead>
        <tbody>
          ${resource.recent_tasks.map(t => `
            <tr>
              <td>${t.id.slice(0, 8)}</td>
              <td><span class="badge status-${t.status}">${t.status}</span></td>
              <td>${t.reused_audio || t.reused_transcript ? '是' : '否'}</td>
              <td>${formatDate(t.created_at)}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    `;
    showModal(html);
  } catch (error) {
    showToast('加载资源详情失败: ' + error.message, 'error');
  }
}

async function deleteResource(videoId) {
  if (!confirm(`确定要删除视频资源 ${videoId} 吗？\n\n此操作将删除关联的所有文件，但保留任务历史。`)) return;

  try {
    await apiRequest(`/api/v1/video-resources/${videoId}`, { method: 'DELETE' });
    showToast('资源已删除', 'success');
    loadResources();
  } catch (error) {
    showToast('删除资源失败: ' + error.message, 'error');
  }
}

// ==================== 创建任务 ====================
async function handleCreateTask(event) {
  event.preventDefault();
  const form = event.target;
  const formData = new FormData(form);

  const payload = {
    video_url: formData.get('video_url'),
    include_audio: formData.get('include_audio') === 'on',
    include_transcript: formData.get('include_transcript') === 'on',
    priority: formData.get('priority'),
  };

  try {
    const response = await apiRequest('/api/v1/tasks', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    showToast('任务创建成功', 'success');
    form.reset();
    switchTab('queue');
  } catch (error) {
    showToast('创建任务失败: ' + error.message, 'error');
  }
}

// ==================== 人工上传（复用原有逻辑）====================
async function checkVideoStatus() {
  const videoUrl = document.getElementById('video-url').value.trim();
  if (!videoUrl) return;

  const videoId = extractVideoId(videoUrl);
  if (!videoId) {
    showStatusCheck('无效的 YouTube URL', 'error');
    return;
  }

  try {
    const response = await apiRequest(`/api/v1/video-status/${videoId}`);
    displayVideoStatus(response);
  } catch (error) {
    showStatusCheck('无法获取视频状态', 'warning');
  }
}

function displayVideoStatus(status) {
  const uploadBtn = document.getElementById('upload-btn');

  if (status.has_audio) {
    showStatusCheck(
      `⚠️ 此视频已有音频文件<br>来源：${status.audio_source === 'manual' ? '人工上传' : '自动下载'}<br>上传时间：${formatDate(status.audio_created_at)}<br><strong>无法上传新的音频文件</strong>`,
      'error'
    );
    uploadBtn.disabled = true;
  } else if (status.has_transcript) {
    showStatusCheck(
      `ℹ️ 此视频已有字幕文件<br>来源：${status.transcript_source === 'manual' ? '人工上传' : '自动下载'}<br>上传音频后将形成完整资源（音频+字幕）`,
      'info'
    );
    uploadBtn.disabled = false;
  } else {
    showStatusCheck('✓ 此视频尚无资源，可以上传', 'success');
    uploadBtn.disabled = false;
  }
}

function showStatusCheck(message, type) {
  const el = document.getElementById('status-check');
  el.innerHTML = `<p class="${type}">${message}</p>`;
}

async function handleUpload(event) {
  event.preventDefault();
  const form = event.target;
  const formData = new FormData(form);

  const progressDiv = document.getElementById('upload-progress');
  const progressBar = progressDiv.querySelector('.progress-bar');
  const progressText = progressDiv.querySelector('.progress-text');
  const uploadBtn = document.getElementById('upload-btn');

  uploadBtn.disabled = true;
  progressDiv.style.display = 'block';

  try {
    const xhr = new XMLHttpRequest();

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) {
        const percent = (e.loaded / e.total) * 100;
        progressBar.style.width = percent + '%';
        progressText.textContent = `上传中 ${percent.toFixed(0)}%`;
      }
    };

    xhr.onload = () => {
      if (xhr.status === 200 || xhr.status === 201) {
        showToast('上传成功', 'success');
        form.reset();
        showStatusCheck('✓ 上传前可以先检查视频资源状态', 'info');
        switchTab('resources');
      } else {
        const error = JSON.parse(xhr.responseText);
        showToast('上传失败: ' + error.detail, 'error');
      }
      progressDiv.style.display = 'none';
      uploadBtn.disabled = false;
    };

    xhr.onerror = () => {
      showToast('上传失败: 网络错误', 'error');
      progressDiv.style.display = 'none';
      uploadBtn.disabled = false;
    };

    xhr.open('POST', '/api/v1/manual-upload');
    xhr.setRequestHeader('X-API-Key', state.apiKey);
    xhr.send(formData);

  } catch (error) {
    showToast('上传失败: ' + error.message, 'error');
    progressDiv.style.display = 'none';
    uploadBtn.disabled = false;
  }
}

// ==================== Cookie 管理 ====================
async function loadCookie() {
  try {
    const data = await apiRequest('/api/v1/settings/cookie');

    document.getElementById('cookie-path').textContent = data.path;
    document.getElementById('cookie-size').textContent = data.exists ? formatBytes(data.size) : '不存在';
    document.getElementById('cookie-updated').textContent = data.last_modified ? formatDate(data.last_modified) : '-';
    document.getElementById('cookie-content').value = data.content || '';

    if (!data.exists) {
      showToast('Cookie 文件不存在，可创建新文件', 'info');
    }
  } catch (error) {
    showToast('加载 Cookie 失败: ' + error.message, 'error');
  }
}

async function saveCookie() {
  const content = document.getElementById('cookie-content').value;

  if (!content.trim()) {
    showToast('Cookie 内容不能为空', 'warning');
    return;
  }

  try {
    const response = await apiRequest('/api/v1/settings/cookie', {
      method: 'PUT',
      body: JSON.stringify({
        content,
        create_backup: true,
      }),
    });

    showToast('Cookie 已保存', 'success');

    if (response.warnings && response.warnings.length > 0) {
      const warningsDiv = document.getElementById('cookie-warnings');
      warningsDiv.innerHTML = `<div class="warning-box"><strong>警告:</strong><ul>${response.warnings.map(w => `<li>${escapeHtml(w)}</li>`).join('')}</ul></div>`;
    }

    loadCookie();
  } catch (error) {
    showToast('保存 Cookie 失败: ' + error.message, 'error');
  }
}

async function validateCookie() {
  const content = document.getElementById('cookie-content').value;

  if (!content.trim()) {
    showToast('Cookie 内容不能为空', 'warning');
    return;
  }

  try {
    const result = await apiRequest('/api/v1/settings/cookie/validate', {
      method: 'POST',
      body: JSON.stringify({ content }),
    });

    const warningsDiv = document.getElementById('cookie-warnings');

    if (result.valid) {
      warningsDiv.innerHTML = `<div class="success-box">✓ Cookie 格式有效（${result.line_count} 行有效数据）</div>`;
    } else {
      warningsDiv.innerHTML = `<div class="error-box"><strong>格式错误:</strong><ul>${result.errors.map(e => `<li>${escapeHtml(e)}</li>`).join('')}</ul></div>`;
    }

    if (result.warnings.length > 0) {
      warningsDiv.innerHTML += `<div class="warning-box"><strong>警告:</strong><ul>${result.warnings.map(w => `<li>${escapeHtml(w)}</li>`).join('')}</ul></div>`;
    }

  } catch (error) {
    showToast('验证失败: ' + error.message, 'error');
  }
}

function clearCookieEditor() {
  if (!confirm('确定要清空 Cookie 编辑器吗？')) return;
  document.getElementById('cookie-content').value = '';
  document.getElementById('cookie-warnings').innerHTML = '';
}

// ==================== 工具函数 ====================
function extractVideoId(url) {
  const patterns = [
    /(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/)([a-zA-Z0-9_-]{11})/,
  ];

  for (const pattern of patterns) {
    const match = url.match(pattern);
    if (match) return match[1];
  }
  return null;
}

function formatBytes(bytes) {
  if (!bytes) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

function formatDate(dateString) {
  if (!dateString) return '-';
  const date = new Date(dateString);
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function formatDuration(seconds) {
  if (!seconds) return '-';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return h > 0 ? `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}` : `${m}:${s.toString().padStart(2, '0')}`;
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

function getTaskType(task) {
  if (task.request_mode?.include_audio && task.request_mode?.include_transcript) return '音频+字幕';
  if (task.request_mode?.include_audio) return '仅音频';
  if (task.request_mode?.include_transcript) return '仅字幕';
  return '-';
}

function getCacheInfo(task) {
  const parts = [];
  if (task.result?.reused_audio) parts.push('音频');
  if (task.result?.reused_transcript) parts.push('字幕');
  return parts.length > 0 ? parts.join('+') : '-';
}

// ==================== Modal 和 Toast ====================
function showModal(html) {
  document.getElementById('modal-body').innerHTML = html;
  document.getElementById('modal').style.display = 'flex';
}

function closeModal() {
  document.getElementById('modal').style.display = 'none';
}

function showToast(message, type = 'info') {
  const toast = document.getElementById('toast');
  toast.textContent = message;
  toast.className = `toast ${type} show`;

  setTimeout(() => {
    toast.classList.remove('show');
  }, 3000);
}
