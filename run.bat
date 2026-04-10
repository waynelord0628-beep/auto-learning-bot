@echo off
title 行政效能領航員 - 自動啟動器
setlocal

echo [*] 正在檢查環境...

:: 檢查是否有安裝 uv (推薦)
where uv >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    echo [+] 偵測到 uv，正在使用 uv 快速啟動...
    uv run app.py --headless
) else (
    echo [!] 未偵測到 uv，使用標準 python 啟動...
    echo [*] 正在確保依賴套件已安裝...
    python -m pip install -r requirements.txt --quiet
    python app.py --headless
)

pause
