# 关键问题分析：RATE_LIMITED（Cookie 已更新）

## 🔴 当前状态

- ❌ 所有错误都是 `RATE_LIMITED`
- ✅ Cookie 刚刚更换过（排除 Cookie 过期）
- ⚠️ 配置：间隔 300-900 秒

## 🎯 根本原因分析

既然 Cookie 是新的还是 RATE_LIMITED，问题出在：

### 1. 任务间隔仍然太短 ⭐⭐⭐⭐⭐

**你当前配置**：
```bash
TASK_INTERVAL_MIN=300   # 5分钟
TASK_INTERVAL_MAX=900   # 15分钟
```

**问题**：
- YouTube 的限流阈值不是固定的
- 即使有 Cookie，短时间内频繁请求仍会触发限流
- 5-15分钟的间隔对于服务器环境可能还是太激进

**证据**：
- 你说"有时成功有时失败" → 说明不是完全被封，是频率问题
- 所有错误都是 RATE_LIMITED → 不是网络或 PO Token 问题

**建议修改**：
```bash
TASK_INTERVAL_MIN=900    # 15分钟
TASK_INTERVAL_MAX=2700   # 45分钟
```

### 2. 任务积压问题 ⭐⭐⭐⭐

**可能情况**：
- 如果有大量待处理任务在队列中
- 即使单个任务间隔够长，但总体频率仍然很高
- YouTube 可能按 IP 的总请求频率限流

**验证方法**：
```bash
# 查看待处理任务数量
curl -H "X-API-Key: XLxGQrOGVV1o6FV1iFQEK32wQLhI6yhJotA7uulCGZScaygiuWcEHBZbyUQJjKKM" \
  http://192.168.31.218:8300/api/v1/tasks | jq '.[] | select(.status=="pending") | .id' | wc -l
```

**解决方案**：
- 暂停新任务提交
- 让现有任务慢慢消化完
- 观察成功率是否提升

### 3. PO Token 服务间歇性失败 ⭐⭐⭐

**你的配置**：
```bash
POT_SERVER_URL=http://192.168.31.218:4416
```

**潜在问题**：
- 如果 PO Token 获取失败，YouTube 会将请求识别为 bot
- 即使有 Cookie，没有 PO Token 也会被限流
- 内网 IP 可能存在间歇性连接问题

**验证方法**：
```bash
# 查看是否有 POT_TOKEN_FAILED 错误
docker logs youtube-api --tail 500 | grep POT

# 测试 PO Token 服务稳定性
for i in {1..10}; do
  echo "Test $i:"
  docker exec youtube-api curl -s -o /dev/null -w "HTTP %{http_code} - Time: %{time_total}s\n" http://192.168.31.218:4416/health
  sleep 2
done
```

**如果有问题**：
- 查看 pot-provider 日志：`docker logs pot-provider`
- 考虑重启 pot-provider：`docker restart pot-provider`

### 4. Cookie 虽然是新的，但可能有问题 ⭐⭐

**检查 Cookie 质量**：

```bash
# 1. 检查 Cookie 格式
docker exec youtube-api head -n 10 /app/data/cookies.txt

# 2. 检查 Cookie 数量（应该有多个）
docker exec youtube-api grep -v "^#" /app/data/cookies.txt | grep -v "^$" | wc -l

# 3. 检查关键 Cookie（SAPISID, HSID 等）
docker exec youtube-api grep -E "(SAPISID|HSID|SSID|SID)" /app/data/cookies.txt
```

**Cookie 可能的问题**：
- ❌ 只导出了部分 Cookie（数量太少）
- ❌ 导出时浏览器没有登录 YouTube
- ❌ Cookie 格式不是 Netscape 格式
- ❌ Cookie 来自不同的账号（不匹配）

**重新导出正确的 Cookie**：
1. **完全退出 Chrome 浏览器**
2. 重新打开，访问 YouTube 并登录
3. 看几个视频（激活 session）
4. 使用插件导出 **youtube.com** 的 Cookie
5. 确认文件大小应该 > 5KB

### 5. IP 被 YouTube 限制 ⭐⭐

**验证方法**：
```bash
# 用另一台机器或手机（相同网络）访问 YouTube
# 看是否正常
```

**如果 IP 被限制**：
- 配置代理（如果有）
- 或等待限制自动解除（通常 24-48 小时）

## 🔧 修复方案（按优先级）

### 方案 1：大幅增加任务间隔 ⭐⭐⭐⭐⭐

**这是最可能有效的方案！**

```bash
# 编辑 docker/.env
TASK_INTERVAL_MIN=900    # 15分钟
TASK_INTERVAL_MAX=2700   # 45分钟

# 重启服务
docker-compose -f docker/docker-compose.prod.yml restart youtube-api

# 观察效果（等待 1-2 小时，创建少量测试任务）
```

**为什么这样设置**：
- 900-2700 秒 = 15-45 分钟
- 配合自适应频控机制（代码已优化）
- 限流时会自动延长到 60-180 分钟
- 成功后会逐步缩短回 15-45 分钟

### 方案 2：暂停任务，清理队列 ⭐⭐⭐⭐

```bash
# 1. 停止服务
docker-compose -f docker/docker-compose.prod.yml stop youtube-api

# 2. 等待 1-2 小时（让 YouTube 限流冷却）

# 3. 重启服务
docker-compose -f docker/docker-compose.prod.yml start youtube-api

# 4. 只提交少量测试任务（1-2个）观察成功率
```

### 方案 3：验证并修复 PO Token 服务 ⭐⭐⭐

```bash
# 1. 查看 pot-provider 日志
docker logs pot-provider --tail 100

# 2. 测试连接稳定性（运行上面的循环测试）

# 3. 如果有问题，重启 pot-provider
docker restart pot-provider

# 4. 重启 youtube-api
docker-compose -f docker/docker-compose.prod.yml restart youtube-api
```

### 方案 4：重新导出高质量 Cookie ⭐⭐

即使刚换过，也建议按上面的步骤重新导出一次，确保：
- ✅ 浏览器已登录
- ✅ 导出了完整的 Cookie
- ✅ 文件大小 > 5KB
- ✅ 包含关键 Cookie（SAPISID 等）

### 方案 5：配置代理（如果有）⭐

```bash
# 编辑 docker/.env
HTTP_PROXY=http://your-proxy:port
HTTPS_PROXY=http://your-proxy:port

# 重启服务
docker-compose -f docker/docker-compose.prod.yml restart youtube-api
```

## 📊 验证效果

### 修改后的验证步骤

```bash
# 1. 重启后等待 15 分钟（不要立即测试）

# 2. 创建 1 个测试任务
curl -X POST http://192.168.31.218:8300/api/v1/tasks \
  -H "X-API-Key: XLxGQrOGVV1o6FV1iFQEK32wQLhI6yhJotA7uulCGZScaygiuWcEHBZbyUQJjKKM" \
  -H "Content-Type: application/json" \
  -d '{"video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "include_audio": true}'

# 3. 查看日志
docker logs -f youtube-api | grep -E "(Task|completed|failed|RATE)"

# 4. 如果成功，等待 20 分钟后再创建下一个任务

# 5. 连续成功 5 个任务 → 问题解决 ✅
```

## 🎯 我的强烈建议

基于"所有错误都是 RATE_LIMITED + 刚换过 cookies"这个情况，我 **80% 确定**问题是：

**任务间隔太短 + 可能有任务积压**

**立即执行**：

1. ✅ **修改间隔到 900-2700 秒**（15-45分钟）
2. ✅ **停止服务 1-2 小时**（让限流冷却）
3. ✅ **重启后只提交少量测试任务**（1-2个）
4. ✅ **观察成功率**

如果这样还不行，那就是：
- PO Token 服务有问题
- 或 IP 被严重限制（需要代理）

## 📞 诊断工具

运行这个可以全面检查：

```bash
python scripts/check_production_config.py docker/.env
```

---

**更新时间**：2026-01-25
**针对问题**：RATE_LIMITED（Cookie 已更新）
