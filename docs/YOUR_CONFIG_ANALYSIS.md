# 你的配置快速诊断

## 运行诊断

```bash
python scripts/check_production_config.py docker/.env
```

这个工具会自动检查：
- ✅ PO Token 服务连接（http://192.168.31.218:4416）
- ✅ Cookie 文件状态（./data/cookies.txt）
- ✅ 任务间隔配置
- ✅ 生成针对性修复建议

## 关键配置检查

### 1. PO Token 服务

```bash
POT_SERVER_URL=http://192.168.31.218:4416
```

**快速测试**：
```bash
# 从容器内测试连接
docker exec youtube-api curl -v http://192.168.31.218:4416/health

# 查看 pot-provider 日志
docker logs pot-provider --tail 50
```

### 2. Cookie 文件

```bash
COOKIE_FILE=./data/cookies.txt
```

**验证 Cookie**：
```bash
# 检查文件是否存在
docker exec youtube-api ls -la /app/data/cookies.txt

# 检查格式和数量
docker exec youtube-api head -n 5 /app/data/cookies.txt
docker exec youtube-api grep -v "^#" /app/data/cookies.txt | wc -l
```

**如果 Cookie 有问题**：
1. 完全退出 Chrome
2. 重新打开并登录 YouTube
3. 看几个视频激活 session
4. 用插件导出 youtube.com 的 Cookie
5. 确认文件大小 > 5KB

### 3. 任务间隔

```bash
当前: TASK_INTERVAL_MIN=300, MAX=900  # 5-15分钟
建议: TASK_INTERVAL_MIN=900, MAX=2700  # 15-45分钟
```

**如果全是 RATE_LIMITED 错误** → 间隔太短，立即修改！

## 常见问题

### Q: 所有错误都是 RATE_LIMITED，Cookie 是新的

**A: 任务间隔太短**

修改 `docker/.env`：
```bash
TASK_INTERVAL_MIN=900
TASK_INTERVAL_MAX=2700
```

然后：
```bash
# 停止服务 1-2 小时（让限流冷却）
docker-compose -f docker/docker-compose.prod.yml stop youtube-api

# 重启
docker-compose -f docker/docker-compose.prod.yml start youtube-api

# 只提交 1-2 个测试任务观察
```

### Q: PO Token 服务连接失败

**A: 检查网络和服务状态**

```bash
# 确认服务运行
docker ps | grep pot-provider

# 重启服务
docker restart pot-provider

# 重新测试连接
docker exec youtube-api curl http://192.168.31.218:4416/health
```

### Q: Cookie 文件找不到

**A: 检查路径和挂载**

```bash
# 确认文件在宿主机存在
ls -la docker/data/cookies.txt

# 确认容器内能访问
docker exec youtube-api cat /app/data/cookies.txt | head -n 5

# 检查 docker-compose.prod.yml 中的挂载配置
```

## 修复优先级

1. **增加任务间隔** ⭐⭐⭐⭐⭐（最重要）
2. **验证 Cookie 质量** ⭐⭐⭐⭐
3. **检查 PO Token 服务** ⭐⭐⭐
4. **配置代理**（如果 IP 被限）⭐⭐

详细分析见：[CRITICAL_FIX.md](./CRITICAL_FIX.md)
