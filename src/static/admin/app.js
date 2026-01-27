// ==================== 全局变量 ====================
let currentPage = 1;
const pageSize = 20;

// ==================== API Key 管理 ====================

function getApiKey() {
    return localStorage.getItem('apiKey') || '';
}

function saveApiKey() {
    const apiKey = document.getElementById('api-key').value.trim();
    if (!apiKey) {
        showToast('请输入 API Key', 'warning');
        return;
    }

    localStorage.setItem('apiKey', apiKey);
    showToast('API Key 已保存', 'success');
    refreshList();
}

function clearApiKey() {
    localStorage.removeItem('apiKey');
    document.getElementById('api-key').value = '';
    showToast('API Key 已清除', 'info');
}

function checkApiKey() {
    const apiKey = getApiKey();
    if (!apiKey) {
        showToast('请先设置 API Key', 'warning');
        return false;
    }
    return true;
}

window.addEventListener('DOMContentLoaded', () => {
    const apiKey = getApiKey();
    if (apiKey) {
        document.getElementById('api-key').value = apiKey;
        refreshList();
    }
});

// ==================== 视频状态检查 ====================

async function checkVideoStatus() {
    const videoUrl = document.getElementById('video-url').value.trim();
    if (!videoUrl) {
        return;
    }

    if (!checkApiKey()) {
        return;
    }

    const videoId = extractVideoId(videoUrl);
    if (!videoId) {
        showStatusCheck('无效的 YouTube URL', 'error');
        return;
    }

    try {
        const response = await fetch(`/api/v1/video-status/${videoId}`, {
            headers: {
                'X-API-Key': getApiKey()
            }
        });

        if (response.ok) {
            const data = await response.json();
            displayVideoStatus(data);
        } else {
            showStatusCheck('无法获取视频状态', 'warning');
        }
    } catch (error) {
        console.error('Error checking video status:', error);
        showStatusCheck('网络错误', 'error');
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
    const statusDiv = document.getElementById('status-check');
    statusDiv.innerHTML = `<p>${message}</p>`;
    statusDiv.className = `status-check ${type}`;
}

function extractVideoId(url) {
    const patterns = [
        /(?:youtube\.com\/watch\?v=|youtu\.be\/)([a-zA-Z0-9_-]{11})/,
        /youtube\.com\/embed\/([a-zA-Z0-9_-]{11})/,
    ];

    for (const pattern of patterns) {
        const match = url.match(pattern);
        if (match) {
            return match[1];
        }
    }

    return null;
}

// ==================== 文件上传 ====================

async function handleUpload(event) {
    event.preventDefault();

    if (!checkApiKey()) {
        return;
    }

    const form = document.getElementById('upload-form');
    const formData = new FormData(form);

    const progressDiv = document.getElementById('upload-progress');
    const progressBar = progressDiv.querySelector('.progress-bar');
    const progressText = progressDiv.querySelector('.progress-text');
    progressDiv.style.display = 'block';
    progressBar.style.width = '0%';
    progressText.textContent = '上传中...';

    const uploadBtn = document.getElementById('upload-btn');
    uploadBtn.disabled = true;

    try {
        let progress = 0;
        const progressInterval = setInterval(() => {
            progress += 5;
            if (progress <= 90) {
                progressBar.style.width = `${progress}%`;
            }
        }, 200);

        const response = await fetch('/api/v1/manual-upload', {
            method: 'POST',
            headers: {
                'X-API-Key': getApiKey()
            },
            body: formData
        });

        clearInterval(progressInterval);

        if (response.ok) {
            progressBar.style.width = '100%';
            progressText.textContent = '上传成功！';

            const result = await response.json();
            showToast(`上传成功！视频：${result.video_info?.title || result.video_id}`, 'success');

            resetForm();
            refreshList();

            setTimeout(() => {
                progressDiv.style.display = 'none';
            }, 2000);
        } else {
            const error = await response.json();
            let errorMsg = '上传失败';

            if (error.detail?.error === 'AUDIO_ALREADY_EXISTS') {
                errorMsg = `此视频已有音频文件（来源：${error.detail.existing_source}）`;
            } else if (error.detail?.error === 'INVALID_FILE_FORMAT') {
                errorMsg = '文件格式不支持';
            } else if (error.detail?.error === 'FILE_TOO_LARGE') {
                errorMsg = '文件过大（最大 500MB）';
            } else if (error.detail?.message) {
                errorMsg = error.detail.message;
            }

            progressText.textContent = errorMsg;
            progressBar.style.width = '100%';
            progressBar.style.background = '#dc3545';

            showToast(errorMsg, 'error');

            setTimeout(() => {
                progressDiv.style.display = 'none';
                progressBar.style.background = 'linear-gradient(90deg, #4f46e5 0%, #06b6d4 100%)';
            }, 3000);
        }
    } catch (error) {
        console.error('Upload error:', error);
        progressText.textContent = '网络错误';
        showToast('上传失败：网络错误', 'error');

        setTimeout(() => {
            progressDiv.style.display = 'none';
        }, 3000);
    } finally {
        uploadBtn.disabled = false;
    }
}

function resetForm() {
    document.getElementById('upload-form').reset();
    document.getElementById('status-check').innerHTML = '<p>✓ 上传前可以先检查视频资源状态</p>';
    document.getElementById('status-check').className = 'status-check';
    document.getElementById('upload-btn').disabled = false;
}

// ==================== 列表管理 ====================

async function refreshList() {
    if (!checkApiKey()) {
        return;
    }

    await loadUploadsList(currentPage);
    await loadStats();
}

async function loadUploadsList(page = 1) {
    const tbody = document.getElementById('uploads-tbody');
    tbody.innerHTML = '<tr><td colspan="7" class="loading">加载中...</td></tr>';

    try {
        const offset = (page - 1) * pageSize;
        const response = await fetch(`/api/v1/manual-uploads?limit=${pageSize}&offset=${offset}`, {
            headers: {
                'X-API-Key': getApiKey()
            }
        });

        if (response.ok) {
            const data = await response.json();
            displayUploadsList(data);
            updatePagination(page, data.total);
        } else {
            tbody.innerHTML = '<tr><td colspan="7" class="empty">加载失败</td></tr>';
            showToast('加载列表失败', 'error');
        }
    } catch (error) {
        console.error('Error loading uploads:', error);
        tbody.innerHTML = '<tr><td colspan="7" class="empty">网络错误</td></tr>';
    }
}

function displayUploadsList(data) {
    const tbody = document.getElementById('uploads-tbody');

    if (data.uploads.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="empty">暂无上传记录</td></tr>';
        return;
    }

    tbody.innerHTML = data.uploads.map(upload => `
        <tr>
            <td><code>${upload.video_id}</code></td>
            <td>${escapeHtml(upload.title || '-')}</td>
            <td>${escapeHtml(upload.author || '-')}</td>
            <td>
                ${upload.format ? `<span title="原始格式: ${upload.original_format}">${upload.format}</span>` : '-'}
            </td>
            <td>${formatBytes(upload.size)}</td>
            <td>${formatDate(upload.created_at)}</td>
            <td>
                <div class="action-buttons">
                    <button onclick="previewFile('${upload.file_id}', '${upload.format}')" class="btn-secondary">预览</button>
                    <button onclick="deleteUpload('${upload.video_id}', '${escapeHtml(upload.title || upload.video_id)}')" class="btn-danger">删除</button>
                </div>
            </td>
        </tr>
    `).join('');
}

function updatePagination(page, total) {
    currentPage = page;
    const totalPages = Math.ceil(total / pageSize) || 1;

    document.getElementById('page-info').textContent = `第 ${page} 页 / 共 ${totalPages} 页（总计 ${total} 条）`;
    document.getElementById('prev-btn').disabled = page <= 1;
    document.getElementById('next-btn').disabled = page >= totalPages;
}

function prevPage() {
    if (currentPage > 1) {
        loadUploadsList(currentPage - 1);
    }
}

function nextPage() {
    loadUploadsList(currentPage + 1);
}

// ==================== 统计信息 ====================

async function loadStats() {
    try {
        const response = await fetch(`/api/v1/manual-uploads?limit=1000&offset=0`, {
            headers: {
                'X-API-Key': getApiKey()
            }
        });

        if (response.ok) {
            const data = await response.json();
            const totalSize = data.uploads.reduce((sum, item) => sum + (item.size || 0), 0);

            document.getElementById('total-uploads').textContent = data.total;
            document.getElementById('total-size').textContent = formatBytes(totalSize);
        }
    } catch (error) {
        console.error('Error loading stats:', error);
    }
}

// ==================== 文件操作 ====================

function previewFile(fileId, format) {
    const url = `/api/v1/files/${fileId}.${format}`;
    window.open(url, '_blank');
}

async function deleteUpload(videoId, title) {
    if (!confirm(`确定要删除 "${title}" 的上传记录吗？\n\n此操作将删除音频文件，但不会删除已存在的字幕。`)) {
        return;
    }

    try {
        const response = await fetch(`/api/v1/manual-uploads/${videoId}`, {
            method: 'DELETE',
            headers: {
                'X-API-Key': getApiKey()
            }
        });

        if (response.ok) {
            showToast('删除成功', 'success');
            refreshList();
        } else {
            const error = await response.json();
            showToast(`删除失败：${error.detail || '未知错误'}`, 'error');
        }
    } catch (error) {
        console.error('Error deleting upload:', error);
        showToast('删除失败：网络错误', 'error');
    }
}

// ==================== 工具函数 ====================

function formatBytes(bytes) {
    if (!bytes || bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return (bytes / Math.pow(k, i)).toFixed(2) + ' ' + sizes[i];
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

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function showToast(message, type = 'info') {
    const toast = document.getElementById('toast');
    toast.textContent = message;
    toast.className = `toast ${type} show`;

    setTimeout(() => {
        toast.classList.remove('show');
    }, 3000);
}
