"""最简单的测试。"""

import sys
from pathlib import Path

# 获取项目根目录（从 tests/integration/ 向上两级）
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

print("Test 1: Import Settings")
try:
    from src.config import Settings

    print("[PASS] Settings imported")
except Exception as e:
    print(f"[FAIL] {e}")
    sys.exit(1)

print("\nTest 2: Create Settings")
try:
    settings = Settings(
        api_key="test-key", cdp_enabled=True, cdp_urls="http://127.0.0.1:9222"
    )
    print("[PASS] Settings created")
    print(f"  CDP Enabled: {settings.cdp_enabled}")
    print(f"  CDP URLs: {settings.cdp_url_list}")
except Exception as e:
    print(f"[FAIL] {e}")
    sys.exit(1)

print("\nTest 3: Check CDP configs")
try:
    print(f"  CDP Timeout: {settings.cdp_timeout}")
    print(f"  Health Check Interval: {settings.cdp_health_check_interval}")
    print(f"  Circuit Failure Threshold: {settings.cdp_circuit_failure_threshold}")
    print(f"  Circuit Timeout: {settings.cdp_circuit_timeout}")
    print(f"  Use Curl CFFI: {settings.cdp_use_curl_cffi}")
    print(f"  Enable POT Token: {settings.cdp_enable_pot_token}")
    print(f"  Enable Multipart: {settings.cdp_enable_multipart}")
    print("[PASS] All CDP configs accessible")
except Exception as e:
    print(f"[FAIL] {e}")
    sys.exit(1)

print("\nTest 4: Check POT Token was integrated")
try:
    import inspect

    # 读取文件内容检查（CDP 下载器已模块化）
    cdp_file = project_root / "src" / "downloaders" / "cdp" / "downloader.py"
    content = cdp_file.read_text(encoding="utf-8")

    checks = [
        ("_get_pot_token", "_get_pot_token" in content),
        ("pot_token获取", "pot_token" in content and "get_pot" in content),
        ("cdp_enable_pot_token配置", "cdp_enable_pot_token" in content),
    ]

    for name, result in checks:
        status = "[PASS]" if result else "[FAIL]"
        print(f"  {status} {name}")

    if all(r for _, r in checks):
        print("[PASS] POT Token integration found in code")
    else:
        print("[FAIL] POT Token integration incomplete")
        sys.exit(1)

except Exception as e:
    print(f"[FAIL] {e}")
    sys.exit(1)

print("\nTest 5: Check health_check was implemented")
try:
    if "async def health_check" in content:
        print("[PASS] health_check method found")
    else:
        print("[FAIL] health_check method not found")
        sys.exit(1)

    if "CDPHealthStatus" in content:
        print("[PASS] CDPHealthStatus return type found")
    else:
        print("[FAIL] CDPHealthStatus not found")
        sys.exit(1)

except Exception as e:
    print(f"[FAIL] {e}")
    sys.exit(1)

print("\nTest 6: Check notification methods")
try:
    notify_file = project_root / "src" / "services" / "notify.py"
    notify_content = notify_file.read_text(encoding="utf-8")

    checks = [
        (
            "notify_cdp_connection_failed",
            "notify_cdp_connection_failed" in notify_content,
        ),
        (
            "notify_cdp_circuit_breaker_open",
            "notify_cdp_circuit_breaker_open" in notify_content,
        ),
        ("notify_cdp_recovered", "notify_cdp_recovered" in notify_content),
    ]

    for name, result in checks:
        status = "[PASS]" if result else "[FAIL]"
        print(f"  {status} {name}")

    if all(r for _, r in checks):
        print("[PASS] All notification methods found")
    else:
        print("[FAIL] Some notification methods missing")
        sys.exit(1)

except Exception as e:
    print(f"[FAIL] {e}")
    sys.exit(1)

print("\n" + "=" * 60)
print("All tests passed!")
print("=" * 60)
print("\nSummary:")
print("  [PASS] Settings and configs")
print("  [PASS] POT Token integration")
print("  [PASS] health_check implementation")
print("  [PASS] Notification methods")
