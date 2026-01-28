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
  timezone: 'Asia/Shanghai', // 默认时区，将从服务器加载
  version: '',  // 系统版本号
  buildTime: '',  // 构建时间
};

// ==================== 初始化 ====================
window.addEventListener('DOMContentLoaded', async () => {
  // 加载服务器配置（时区等）
  await loadServerConfig();

  if (state.apiKey) {
    document.getElementById('api-key').value = state.apiKey;
  }
  // 从 URL hash 读取初始 tab
  const hash = window.location.hash.slice(1);
  const validTabs = ['queue', 'history', 'resources', 'create', 'settings'];
  const initialTab = (hash && validTabs.includes(hash)) ? hash : state.currentTab;

  // 初始化 tab（会自动更新 URL）
  // 强制执行初始化，所以先清空 currentTab
  state.currentTab = '';
  doSwitchTab(initialTab);
});

// 加载服务器配置
async function loadServerConfig() {
  try {
    const response = await fetch('/api/v1/settings/config');
    if (response.ok) {
      const config = await response.json();
      state.timezone = config.timezone;
      state.version = config.version;
      state.buildTime = config.build_time;
      console.log(`Loaded server config - timezone: ${state.timezone}, version: ${state.version}, build: ${state.buildTime}`);

      // 更新页面底部的构建信息显示
      updateBuildInfo();
    }
  } catch (error) {
    console.warn('Failed to load server config, using default timezone:', error);
  }
}

// 更新页面底部的构建信息
function updateBuildInfo() {
  const buildInfoElement = document.getElementById('build-info');
  if (buildInfoElement && state.buildTime) {
    // 格式化构建时间显示
    let buildTimeDisplay = state.buildTime;
    if (state.buildTime !== 'development') {
      try {
        buildTimeDisplay = formatDate(state.buildTime);
      } catch (e) {
        // 如果解析失败，使用原始字符串
      }
    }
    buildInfoElement.textContent = `v${state.version} | 构建时间: ${buildTimeDisplay}`;
  }
}

// 监听 URL hash 变化
window.addEventListener('hashchange', () => {
  const hash = window.location.hash.slice(1);
  const validTabs = ['queue', 'history', 'resources', 'create', 'settings'];
  if (hash && validTabs.includes(hash) && hash !== state.currentTab) {
    doSwitchTab(hash);
  }
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
  window.location.hash = tabName;
}

function doSwitchTab(tabName) {
  if (state.currentTab === tabName) return;

  state.currentTab = tabName;

  // Update Tab Buttons (Matched with new .tab-item class)
  document.querySelectorAll('.tab-item').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === tabName);
  });

  // Update Content
  document.querySelectorAll('.tab-content').forEach(content => {
    content.classList.toggle('active', content.id === `tab-${tabName}`);
  });

  if (window.location.hash !== `#${tabName}`) {
    window.location.hash = tabName;
  }

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
    container.innerHTML = '<div class="empty-state"><p class="loading-state">暂无正在下载的任务</p></div>';
    return;
  }

  container.innerHTML = tasks.map(task => `
    <div class="task-item downloading">
      <div class="task-header">
        <span class="badge priority-${task.priority}">${task.priority}</span>
        <strong class="task-title">${escapeHtml(task.video_info?.title || task.video_id)}</strong>
      </div>
      <div class="task-meta">
        <span class="meta-item">Video ID: ${task.video_id}</span>
        <span class="meta-item">类型: ${getTaskType(task)}</span>
        <span class="meta-item">开始时间: ${formatDate(task.started_at)}</span>
      </div>
    </div>
  `).join('');
}

function renderPendingTasks(tasks) {
  const container = document.getElementById('pending-tasks');
  if (tasks.length === 0) {
    container.innerHTML = '<div class="empty-state"><p class="loading-state">队列为空</p></div>';
    return;
  }

  container.innerHTML = tasks.map((task, index) => `
    <div class="task-item">
      <div class="task-header">
        <div class="meta-item"><span class="badge priority-${task.priority}">${task.priority}</span> <span style="font-size:0.8em;color:#999">#${index + 1}</span></div>
        <strong class="task-title" style="flex:1;margin:0 10px">${escapeHtml(task.video_info?.title || task.video_id)}</strong>
        <button class="btn-danger-sm" onclick="cancelTask('${task.task_id}')">取消</button>
      </div>
      <div class="task-meta">
        <span class="meta-item">Video ID: ${task.video_id}</span>
        <span class="meta-item">类型: ${getTaskType(task)}</span>
        <span class="meta-item">创建时间: ${formatDate(task.created_at)}</span>
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
    tbody.innerHTML = '<tr><td colspan="8" class="loading-state">暂无记录</td></tr>';
    return;
  }

  tbody.innerHTML = tasks.map(task => {
    // 获取请求参数
    const includeAudio = task.request_mode?.include_audio ?? false;
    const includeTranscript = task.request_mode?.include_transcript ?? false;

    // 图标：✓ 表示请求了该资源，✗ 表示未请求
    const audioIcon = includeAudio ? '✓' : '✗';
    const transcriptIcon = includeTranscript ? '✓' : '✗';

    // 根据是否请求添加不同的样式
    const audioStyle = includeAudio ? 'color: #10b981; font-weight: bold;' : 'color: #9ca3af;';
    const transcriptStyle = includeTranscript ? 'color: #10b981; font-weight: bold;' : 'color: #9ca3af;';

    return `
      <tr onclick="showTaskDetail('${task.task_id}')" style="cursor:pointer;">
        <td>${task.video_id}</td>
        <td>${escapeHtml(task.video_info?.title || '-')}</td>
        <td><span class="badge status-${task.status}">${task.status}</span></td>
        <td style="${audioStyle}" title="${includeAudio ? '请求音频' : '未请求音频'}">${audioIcon}</td>
        <td style="${transcriptStyle}" title="${includeTranscript ? '请求字幕' : '未请求字幕'}">${transcriptIcon}</td>
        <td>${getCacheInfo(task)}</td>
        <td>${formatDate(task.created_at)}</td>
        <td>${formatDate(task.completed_at)}</td>
      </tr>
    `;
  }).join('');
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
      <div class="detail-list">
        <div class="detail-item"><span class="detail-label">任务 ID:</span> <span class="detail-value">${task.task_id}</span></div>
        <div class="detail-item"><span class="detail-label">Video ID:</span> <span class="detail-value">${task.video_id}</span></div>
        <div class="detail-item"><span class="detail-label">状态:</span> <span class="detail-value"><span class="badge status-${task.status}">${task.status}</span></span></div>
        <div class="detail-item"><span class="detail-label">优先级:</span> <span class="detail-value">${task.priority}</span></div>
        <div class="detail-item"><span class="detail-label">标题:</span> <span class="detail-value">${escapeHtml(task.video_info?.title || '-')}</span></div>
        <div class="detail-item"><span class="detail-label">作者:</span> <span class="detail-value">${escapeHtml(task.video_info?.author || '-')}</span></div>
        <div class="detail-item"><span class="detail-label">时长:</span> <span class="detail-value">${formatDuration(task.video_info?.duration)}</span></div>
        <div class="detail-item"><span class="detail-label">请求类型:</span> <span class="detail-value">${getTaskType(task)}</span></div>
        <div class="detail-item"><span class="detail-label">缓存命中:</span> <span class="detail-value">${getCacheInfo(task)}</span></div>
        ${task.error_message ? `<div class="detail-item error-box"><span class="detail-label">错误:</span> <span class="detail-value">${escapeHtml(task.error_message)}</span></div>` : ''}
        <div class="detail-item"><span class="detail-label">创建时间:</span> <span class="detail-value">${formatDate(task.created_at)}</span></div>
        <div class="detail-item"><span class="detail-label">开始时间:</span> <span class="detail-value">${formatDate(task.started_at)}</span></div>
        <div class="detail-item"><span class="detail-label">完成时间:</span> <span class="detail-value">${formatDate(task.completed_at)}</span></div>
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
    grid.innerHTML = '<div class="empty-state"><p class="loading-state">暂无视频资源</p></div>';
    return;
  }

  grid.innerHTML = resources.map(res => {
    const title = res.video_info?.title || res.video_id;
    const author = res.video_info?.author || '未知';
    const thumbnail = res.video_info?.thumbnail || `https://i.ytimg.com/vi/${res.video_id}/default.jpg`;
    const duration = formatDuration(res.video_info?.duration);
    const uploadDate = res.video_info?.upload_date ? formatUploadDate(res.video_info.upload_date) : null;
    const description = res.video_info?.description ? truncateText(res.video_info.description, 80) : null;

    return `
      <div class="resource-card" onclick="showResourceDetail('${res.video_id}')" style="cursor: pointer;">
        <div class="thumbnail-wrapper">
          <img src="${thumbnail}" alt="${escapeHtml(title)}" loading="lazy" onerror="this.src='/admin/placeholder.svg'" />
          <span class="duration-badge">${duration}</span>
        </div>
        <div class="resource-info">
          <h3 class="resource-title" title="${escapeHtml(title)}">${escapeHtml(title)}</h3>
          <p class="resource-author">${escapeHtml(author)}</p>
          ${uploadDate ? `<p class="resource-upload-date" style="font-size: 0.85em; color: #6c757d; margin: 4px 0;">上传: ${uploadDate}</p>` : ''}
          ${description ? `<p class="resource-description" style="font-size: 0.85em; color: #868e96; margin: 6px 0; line-height: 1.4;" title="${escapeHtml(res.video_info.description)}">${escapeHtml(description)}</p>` : ''}
          <div class="resource-tags">
             <span class="tag-pill">${res.upload_source}</span>
             <span class="tag-pill">${res.audio_count} 音频</span>
             <span class="tag-pill">${res.transcript_count} 字幕</span>
          </div>
          <div class="resource-footer" onclick="event.stopPropagation()">
            <button class="btn-sm btn-secondary" onclick="showResourceDetail('${res.video_id}')">详情</button>
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
    const youtubeUrl = `https://www.youtube.com/watch?v=${resource.video_id}`;
    const thumbnail = resource.video_info?.thumbnail || `https://i.ytimg.com/vi/${resource.video_id}/maxresdefault.jpg`;

    const html = `
      <h2>视频资源详情</h2>
      ${resource.video_info?.thumbnail ? `
        <div style="text-align: center; margin: 16px 0;">
          <img src="${thumbnail}" alt="视频缩略图" style="max-width: 100%; max-height: 320px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);" onerror="this.style.display='none'" />
        </div>
      ` : ''}
      <div class="detail-list">
        <div class="detail-item">
          <span class="detail-label">Video ID:</span>
          <span class="detail-value">
            ${resource.video_id}
            <a href="${youtubeUrl}" target="_blank" rel="noopener noreferrer" style="margin-left: 8px; color: #5865F2; text-decoration: none; font-weight: 500;">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align: middle; margin-right: 2px;">
                <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path>
                <polyline points="15 3 21 3 21 9"></polyline>
                <line x1="10" y1="14" x2="21" y2="3"></line>
              </svg>
              在 YouTube 查看
            </a>
          </span>
        </div>
        <div class="detail-item"><span class="detail-label">标题:</span> <span class="detail-value">${escapeHtml(resource.video_info?.title || '-')}</span></div>
        <div class="detail-item"><span class="detail-label">作者:</span> <span class="detail-value">${escapeHtml(resource.video_info?.author || '-')}</span></div>
        <div class="detail-item"><span class="detail-label">时长:</span> <span class="detail-value">${formatDuration(resource.video_info?.duration)}</span></div>
        ${resource.video_info?.upload_date ? `
          <div class="detail-item"><span class="detail-label">上传日期:</span> <span class="detail-value">${formatUploadDate(resource.video_info.upload_date)}</span></div>
        ` : ''}
        ${resource.video_info?.view_count ? `
          <div class="detail-item"><span class="detail-label">观看次数:</span> <span class="detail-value">${formatNumber(resource.video_info.view_count)}</span></div>
        ` : ''}
        ${resource.video_info?.description ? `
          <div class="detail-item">
            <span class="detail-label">描述:</span>
            <div class="detail-value" style="max-height: 150px; overflow-y: auto; white-space: pre-wrap; word-break: break-word; padding: 8px; background: #f8f9fa; border-radius: 6px; border: 1px solid #e9ecef;">${escapeHtml(resource.video_info.description)}</div>
          </div>
        ` : ''}
        <div class="detail-item"><span class="detail-label">有原生字幕:</span> <span class="detail-value">${resource.has_native_transcript ? '是' : '否'}</span></div>
        <div class="detail-item"><span class="detail-label">创建时间:</span> <span class="detail-value">${formatDate(resource.created_at)}</span></div>
        <div class="detail-item"><span class="detail-label">更新时间:</span> <span class="detail-value">${formatDate(resource.updated_at)}</span></div>
      </div>
      <h3>文件列表 (${resource.files.length})</h3>
      <div class="file-list">
        ${resource.files.map(f => {
          const fileIcon = f.file_type === 'audio' ?
            '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 18V5l12-2v13"></path><circle cx="6" cy="18" r="3"></circle><circle cx="18" cy="16" r="3"></circle></svg>' :
            '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10 9 9 9 8 9"></polyline></svg>';

          // 生成下载文件名：videoid_title_author.ext
          const customFilenameSrt = generateDownloadFilename(
            resource.video_id,
            resource.video_info?.title,
            resource.video_info?.author,
            'srt'
          );

          const customFilenameTxt = generateDownloadFilename(
            resource.video_id,
            resource.video_info?.title,
            resource.video_info?.author,
            'txt'
          );

          return `
            <div class="file-item">
              <div class="file-icon ${f.file_type}">${fileIcon}</div>
              <div class="file-info">
                <div class="file-name" title="${escapeHtml(f.filename)}">${escapeHtml(truncateText(f.filename, 40))}</div>
                <div class="file-meta">
                  <span class="badge source-${f.upload_source}">${f.upload_source}</span>
                  <span>${formatBytes(f.size)}</span>
                  <span>${f.format || '-'}</span>
                </div>
              </div>
              <div class="file-actions">
                ${f.file_type === 'transcript' ? `
                  <div style="display: flex; gap: 0.5rem;">
                    <a href="/api/v1/files/${f.id}?filename=${encodeURIComponent(customFilenameSrt)}" class="btn-sm btn-secondary" style="text-decoration: none; display: inline-flex; align-items: center; gap: 4px;" title="下载为 SRT 格式">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                        <polyline points="7 10 12 15 17 10"></polyline>
                        <line x1="12" y1="15" x2="12" y2="3"></line>
                      </svg>
                      SRT
                    </a>
                    <a href="/api/v1/files/${f.id}?filename=${encodeURIComponent(customFilenameTxt)}" class="btn-sm btn-ghost" style="text-decoration: none; display: inline-flex; align-items: center; gap: 4px;" title="下载为 TXT 格式">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                        <polyline points="7 10 12 15 17 10"></polyline>
                        <line x1="12" y1="15" x2="12" y2="3"></line>
                      </svg>
                      TXT
                    </a>
                  </div>
                ` : `
                  <span style="color: #adb5bd; font-size: 0.85rem;">音频文件</span>
                `}
              </div>
            </div>
          `;
        }).join('')}
      </div>
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

// ==================== 人工上传 ====================
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
  el.className = `status-check-box ${type}`; // Update class for styling
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
        showStatusCheck('✓ 上传前建议先检查视频状态', 'info');
        switchTab('resources');
      } else {
        try {
          const error = JSON.parse(xhr.responseText);
          showToast('上传失败: ' + error.detail, 'error');
        } catch (e) {
          showToast('上传失败: ' + xhr.responseText, 'error');
        }
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

  // 使用服务器配置的时区格式化时间
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    timeZone: state.timezone,
  });
}

function formatDuration(seconds) {
  if (!seconds) return '-';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return h > 0 ? `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}` : `${m}:${s.toString().padStart(2, '0')}`;
}

function formatUploadDate(uploadDate) {
  if (!uploadDate) return '-';
  // uploadDate 格式为 YYYYMMDD，转换为 YYYY-MM-DD
  if (typeof uploadDate === 'string' && uploadDate.length === 8) {
    const year = uploadDate.slice(0, 4);
    const month = uploadDate.slice(4, 6);
    const day = uploadDate.slice(6, 8);
    return `${year}-${month}-${day}`;
  }
  return uploadDate;
}

function formatNumber(num) {
  if (!num) return '-';
  // 格式化数字，添加千位分隔符
  return num.toLocaleString('zh-CN');
}

function truncateText(text, maxLength) {
  if (!text) return '';
  if (text.length <= maxLength) return text;
  return text.substring(0, maxLength) + '...';
}

function sanitizeFilename(text) {
  if (!text) return '';
  // 移除或替换非法字符：\ / : * ? " < > |
  return text
    .replace(/[\\/:*?"<>|]/g, '_')  // 替换为下划线
    .replace(/\s+/g, '_')            // 空格替换为下划线
    .replace(/_+/g, '_')             // 多个下划线合并为一个
    .replace(/^_|_$/g, '')           // 移除首尾下划线
    .trim();
}

function generateDownloadFilename(videoId, title, author, extension) {
  // 清理各个部分
  const cleanVideoId = videoId || 'unknown';
  const cleanTitle = sanitizeFilename(title) || 'untitled';
  const cleanAuthor = sanitizeFilename(author) || 'unknown';
  const cleanExt = extension.startsWith('.') ? extension : '.' + extension;

  // 拼接文件名：videoid_title_author.ext
  let filename = `${cleanVideoId}_${cleanTitle}_${cleanAuthor}${cleanExt}`;

  // 限制文件名长度（不包括扩展名），最多 200 字符
  const maxLength = 200;
  if (filename.length > maxLength + cleanExt.length) {
    const availableLength = maxLength - cleanVideoId.length - cleanExt.length - 2; // 2 for underscores
    const titleLength = Math.floor(availableLength * 0.6);
    const authorLength = availableLength - titleLength;

    const truncatedTitle = cleanTitle.substring(0, titleLength);
    const truncatedAuthor = cleanAuthor.substring(0, authorLength);

    filename = `${cleanVideoId}_${truncatedTitle}_${truncatedAuthor}${cleanExt}`;
  }

  return filename;
}

function escapeHtml(text) {
  if (!text) return '';
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
  const modal = document.getElementById('modal');
  const body = document.getElementById('modal-body');

  if (html) {
    body.innerHTML = html;
  }

  modal.style.display = 'flex';
  document.body.style.overflow = 'hidden'; // Prevent scrolling
}

function closeModal() {
  document.getElementById('modal').style.display = 'none';
  document.body.style.overflow = '';
}

// Close Modal on Esc key
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    closeModal();
  }
});

function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = `toast ${type}`;

  // Icon based on type
  let icon = '';
  switch (type) {
    case 'success': icon = '<svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"></polyline></svg>'; break;
    case 'error': icon = '<svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>'; break;
    case 'warning': icon = '<svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>'; break;
    default: icon = '<svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>';
  }

  toast.innerHTML = `${icon}<span>${escapeHtml(message)}</span>`;
  container.appendChild(toast);

  // Auto remove
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(10px)'; // Fall down
    setTimeout(() => toast.remove(), 300);
  }, 3000);
}
