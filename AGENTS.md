# Repository Guidelines

## Project Structure & Module Organization
- `src/`：应用主体 —— `api/`（路由 + 鉴权依赖 + Pydantic schema）、`core/`（yt-dlp 封装、下载 Worker）、`db/`（SQLite 连接 + 模型）、`services/`（任务/文件/回调/通知业务逻辑）、`utils/`（日志、工具函数）。
- `tests/`：与 `src/` 平级，按模块分子目录（`test_api/`、`test_core/`、`test_services/` 等）。
- `docs/`：分类文档（`architecture/`、`configuration/`、`guides/`、`operations/`、`development/` 等），入口见 `docs/README.md`。
- `scripts/`：`dev.sh`（Linux/Mac）与 `dev.ps1`（Windows）开发启动脚本，两者需保持同步。
- `docker/`：生产 compose 文件；`docker-compose.dev.yml`：仅启动 pot-provider 的开发依赖。
- `migrations/`：数据库迁移脚本。

## Build, Test, and Development Commands
```bash
uv sync                                                    # 安装依赖
./scripts/dev.sh                                           # 本地开发启动（Linux/Mac）
.\scripts\dev.ps1                                          # 本地开发启动（Windows）
uv run mypy src/                                           # 类型检查（配置见 mypy.ini，优先级高于 pyproject.toml）
uv run pytest tests/ --tb=short -q                          # 运行测试（pytest.ini 已排除 requires_external / manual 标记）
docker-compose -f docker/docker-compose.prod.yml up -d      # 生产部署
```
CI（`.github/workflows/ci.yml`）在 push/PR 到 `main` 时执行：安装 ffmpeg → `uv sync` → `uv run mypy src/` → `uv run pytest tests/ --tb=short -q`（`API_KEY=ci-test-api-key`）。

## Coding Style & Naming Conventions
- Python 3.11+，包管理统一用 `uv`，不使用 pip。
- 代码中禁止使用 emoji。
- 类型检查用 mypy（`disallow_untyped_defs = False` 但 `check_untyped_defs = True`；`tests.*` 忽略类型错误）。
- 需要跨平台（Windows + Linux/Mac）运行时注意文件编码与路径问题 —— 仓库同时维护 `dev.sh`/`dev.ps1` 两套开发脚本。

## Testing Guidelines
- 测试目录 `tests/`，文件名 `test_*.py`；标记：`integration`、`slow`、`requires_external`（需要外部服务）、`manual`（手动诊断，需显式指定运行）。
- 默认 `addopts` 已排除 `requires_external` 和 `manual` 标记的用例，日常 `pytest` 不会误跑到需要 Chrome/TikHub/YouTube 真实调用的用例。
- 出于性能考虑，优先运行单个测试文件而非整个套件。

## Commit & PR Guidelines
- 提交信息遵循 Conventional Commits 风格：`feat/fix/chore/build/ci(scope): 说明`（见 git log 历史，部分含 issue 号如 `(#6)`）。
- **Git 操作安全规则**（历史事故沉淀，务必遵守）：
  - 严禁使用 `rm -rf .git` 或类似命令删除 git 仓库目录。
  - 清理 git 历史必须用 `git filter-repo`，不要用删除重建的方式。
  - 以下高风险操作执行前必须向用户说明后果并等待明确确认：删除 `.git`、`git push --force`、重写历史（`filter-repo`/`rebase`/`reset --hard`）、删除远程分支。
  - 任何可能导致数据丢失的操作前，必须先备份或确认远程仓库状态。

## Security & Configuration Tips
- API Key 通过 `X-API-Key` 头鉴权，不要提交到仓库；`.env` 参考 `.env.example`。
- Webhook 回调客户端必须验证 HMAC 签名。
- 生产环境使用透明代理，不在代码中硬编码代理地址。
- 默认单并发下载 + 随机任务间隔，避免触发 YouTube 风控；调整前请评估 IP 熔断影响。
