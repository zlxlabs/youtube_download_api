@echo off
REM CDP Chrome 启动脚本（Windows）
REM 此脚本会启动 Chrome 并启用 CDP 调试端口

echo 正在启动 Chrome CDP...
echo.

REM 启动 Chrome with CDP（使用项目内的 profile）
"C:\Program Files\Google\Chrome\Application\chrome.exe" ^
  --remote-debugging-port=9222 ^
  --remote-allow-origins=* ^
  --user-data-dir="D:\MyFolders\Developments\0Python\251212_youtube_download_api\data\chrome-profile" ^
  --no-first-run ^
  --no-default-browser-check

echo.
echo Chrome CDP 已启动（端口 9222）
echo Profile 目录: D:\MyFolders\Developments\0Python\251212_youtube_download_api\data\chrome-profile
echo.
echo 按任意键退出脚本（不会关闭 Chrome）
pause >nul
