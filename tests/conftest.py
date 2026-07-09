import os

# gate CI 用通用 `uv run pytest` 起跑,不带仓库自有 ci.yml 里那个 env(API_KEY=ci-test-api-key)。
# Settings 声明 api_key 必填,缺省时 TestPublicEndpoints::test_config_is_public 在 CI 上必挂。
# 这里给默认值,本地/自有 CI 显式设置的值优先。
os.environ.setdefault("API_KEY", "ci-test-api-key")
