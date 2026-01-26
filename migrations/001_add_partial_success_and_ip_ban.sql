-- Migration: 添加部分成功支持和 IP 熔断状态管理
-- Version: 001
-- Date: 2026-01-27

-- 1. 扩展 tasks 表：添加部分成功相关字段
ALTER TABLE tasks ADD COLUMN partial_success INTEGER DEFAULT 0;
ALTER TABLE tasks ADD COLUMN failure_details TEXT;  -- JSON 格式的失败详情

-- 2. 创建 IP 熔断状态表（单行表，记录当前状态）
CREATE TABLE IF NOT EXISTS ip_ban_status (
    id INTEGER PRIMARY KEY CHECK (id = 1),  -- 确保只有一行
    current_level TEXT NOT NULL DEFAULT 'normal',  -- 'normal' | 'audio_banned' | 'fully_banned'
    banned_at TIMESTAMP,  -- 熔断开始时间
    last_attempt_at TIMESTAMP,  -- 上次尝试时间（被动探测）
    failed_attempts INTEGER DEFAULT 0,  -- 熔断期间失败尝试次数
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 初始化 IP 熔断状态（正常状态）
INSERT OR IGNORE INTO ip_ban_status (id, current_level) VALUES (1, 'normal');

-- 3. 创建 IP 熔断历史表（记录每次熔断事件）
CREATE TABLE IF NOT EXISTS ip_ban_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ban_level TEXT NOT NULL,  -- 'audio_banned' | 'fully_banned'
    trigger_source TEXT,  -- 触发来源: 'audio' | 'transcript' | 'mixed'
    trigger_task_id TEXT,  -- 触发任务 ID
    trigger_downloader TEXT,  -- 触发的下载器
    trigger_error TEXT,  -- 触发错误信息
    banned_at TIMESTAMP NOT NULL,  -- 熔断开始时间
    recovered_at TIMESTAMP,  -- 恢复时间（NULL 表示仍在熔断中）
    duration_seconds INTEGER,  -- 持续时长（秒）
    probe_count INTEGER DEFAULT 0,  -- 探测次数
    recovery_method TEXT,  -- 恢复方式: 'auto_probe' | 'manual' | 'timeout'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 创建索引
CREATE INDEX IF NOT EXISTS idx_ip_ban_history_banned_at ON ip_ban_history(banned_at);
CREATE INDEX IF NOT EXISTS idx_ip_ban_history_ban_level ON ip_ban_history(ban_level);
