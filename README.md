# 行政效能領航員 (AdminEfficiencyPilot)

**版本 V2.0.6** ｜ 數位研習輔助方案

[![Python Version](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

> 協助公務同仁自動完成「公務人員 e 等學習網」的數位研習時數，讓您專注於真正重要的工作。

---

## 目錄

- [功能說明](#功能說明)
- [快速開始（一般使用者）](#快速開始一般使用者)
- [開發者：從原始碼執行](#開發者從原始碼執行)
- [設定檔說明](#設定檔說明)
- [自行打包 .exe](#自行打包-exe)
- [常見問題 FAQ](#常見問題-faq)
- [版本更新記錄](#版本更新記錄)

---

## 功能說明

本工具會自動開啟 Chrome 瀏覽器，登入「公務人員 e 等學習網」，並逐一完成各課程的研習時數、自動作答測驗與問卷，全程無需人工介入。

| 功能 | 說明 |
|------|------|
| 圖形介面 | PySide6 GUI，多帳號管理、設定一鍵完成 |
| 自動登入 | 支援 **eCPA 人事服務網** 與 **我的 E 政府** 兩種登入方式 |
| 自動作答 | 本地題庫 + 可選 AI 補答（OpenAI / 其他相容 API） |
| 自動研習 | 瀏覽所有未達標課程直到時數足夠 |
| 自動測驗/問卷 | 自動完成課程測驗與滿意度問卷 |
| 即時進度 | 顯示每門課的研習百分比與剩餘時間 |
| 閒置登出恢復 | 偵測閒置登出 alert，自動重新登入並繼續當前課程 |
| Session 自動恢復 | Chrome 意外關閉時，自動重建連線繼續執行 |
| 防呆休眠 | 執行期間防止電腦進入睡眠 |
| 版本檢查 | 啟動時自動檢查 GitHub 上是否有新版本 |

---

## 快速開始（一般使用者）

> 一般使用者建議直接使用打包好的 `.exe`，無需安裝 Python。

### 步驟一：下載執行檔

從雲端取得 **`行政效能領航員_V2.0.6.exe`**，放到任意資料夾，例如 `D:\autoLearning\`。

### 步驟二：確認已安裝 Google Chrome

前往 [chrome.google.com](https://www.google.com/chrome/) 下載安裝。

### 步驟三：執行

雙擊 `行政效能領航員_V2.0.6.exe`，依介面提示新增帳號、設定後即可開始。

第一次執行時程式會自動：
- 偵測 Chrome 版本並下載對應的 ChromeDriver 到同目錄 `drivers/`
- 在同目錄建立 `config.json` 儲存帳號與設定

### 中途停止

點擊 GUI 上的「停止」按鈕即可安全停止，程式會自動清理瀏覽器。

---

## 開發者：從原始碼執行

### 前置需求

1. **Python 3.11+**：[python.org](https://www.python.org/downloads/) 下載安裝（記得勾選 *Add Python to PATH*）
2. **Google Chrome**
3. **uv**（推薦的套件管理工具）：

   ```powershell
   powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```

### Clone 與執行

```cmd
git clone https://github.com/waynelord0628-beep/auto-learning-bot.git
cd auto-learning-bot
uv run ui.py
```

或使用 pip：

```cmd
pip install -r requirements.txt
python ui.py
```

> 主程式入口是 `ui.py`（圖形介面），`app.py` 是核心研習引擎（會被 UI 呼叫）。

### 專案結構

```
auto-learning-bot/
├── app.py              ← 核心引擎（研習、作答、登入）
├── ui.py               ← PySide6 圖形介面
├── version.txt         ← 版本號（供線上版本檢查）
├── requirements.txt    ← 套件清單
├── run.bat             ← Windows 一鍵啟動
├── icons/              ← UI 圖示
├── scrapers/           ← 題庫爬蟲（三大來源）
├── tools/              ← 開發測試工具
└── utils/
    ├── helpers.py
    └── webdriver_mgr.py  ← Chrome / ChromeDriver 管理
```

> `config.json`、`questions.db`、`drivers/`、`build/`、`dist/`、`*.spec` 等敏感檔/建置產物不在 repo 中，需自行建立或打包時產生。

---

## 設定檔說明

`config.json` 透過 GUI 自動產生與管理，一般情況不需手動編輯。結構範例：

```json
{
    "accounts": [
        {
            "name": "王小明",
            "login_type": "ecpa",
            "account": "A123456789",
            "password": "your_password"
        }
    ],
    "settings": {
        "headless": false,
        "residence_time": 75,
        "target_percentage": 1.05,
        "blacklist": []
    },
    "ai_provider": "OpenAI",
    "ai_keys": {
        "OpenAI": "sk-..."
    },
    "ai_base_url": "https://api.openai.com/v1",
    "ai_model": "gpt-4o-mini"
}
```

**欄位說明：**

| 欄位 | 說明 |
|------|------|
| `name` | 顯示名稱（自訂） |
| `login_type` | `ecpa`（人事服務網）或 `egov`（我的 E 政府） |
| `account` | 登入帳號 |
| `password` | 登入密碼 |
| `headless` | `false` = 看得到瀏覽器；`true` = 背景執行 |
| `residence_time` | 每個單元停留秒數（建議 ≥ 60） |
| `target_percentage` | 完成比例（`1.0` = 100%，`1.05` = 略超門檻確保通過） |
| `blacklist` | 不要進入的單元名稱清單 |
| `ai_provider` / `ai_keys` / `ai_base_url` / `ai_model` | AI 補答設定（可選，未設定則僅用本地題庫） |

---

## 自行打包 .exe

```cmd
uv run pip install pyinstaller
uv run pyinstaller 行政效能領航員_V2.0.6.spec
```

完成後 `dist/行政效能領航員_V2.0.6.exe` 即為單檔執行版。

---

## 常見問題 FAQ

### 出現「引擎初始化失敗」或 `HTTPConnectionPool` timeout？

通常是 ChromeDriver 與 Chrome 版本不匹配，或舊版 driver 殘留。請：
1. 確認 Chrome 為最新版
2. 刪除執行檔同目錄下的 `drivers/` 資料夾
3. 重新執行，程式會重新下載對應版本

### 登入失敗？

1. 用瀏覽器手動登入確認帳號密碼正確
2. 確認 `login_type` 與實際使用的登入方式一致
3. 若是「我的 E 政府」可能遇到驗證碼，需手動處理

### 跑到一半出現「閒置過久已被登出」？

**V2.0.6 已修復**，程式會自動偵測 alert、重新登入並繼續上課。若仍持續發生請回報 log。

### API 回傳 0 筆課程？

通常是 session 失效。**V2.0.6 已加強 RELOGIN 後的 session 同步**，若仍遇到，重啟程式即可。

### 程式跑完但時數沒有更新？

e 等學習網時數有時需數分鐘才會同步，稍候重新整理頁面確認。

### 想在背景執行不顯示視窗？

GUI 設定中勾選 headless 模式。

### 題庫沒這題的答案怎麼辦？

未命中時：
- 若有設定 AI API：會用 AI 補答
- 若無：略過該題不亂作答

如需手動更新本地題庫：

```cmd
python scrapers/pixnet_to_sqlite.py
python scrapers/peigogo_to_sqlite.py
python scrapers/rodiyer_full_scraper.py
python dedup.py
```

### 出現「No module named ...」錯誤？

```cmd
uv sync
```

或

```cmd
pip install -r requirements.txt
```

---

## 版本更新記錄

| 版本 | 更新內容 |
|------|----------|
| **V2.0.6** | 修復閒置登出 alert 未被攔截導致無限重試的問題；RELOGIN 後補呼叫 `sync_session()` 確保 cookie 同步 |
| V2.0.5 | 修復 `all_exam_done` 未初始化導致核心迴圈 crash；修復 `webdriver_mgr` 的 `parts` 變數未初始化；UI 微調；log 中遮蔽 API key 與密碼 |
| V2.0.0 | 新增圖形介面（PySide6）；內建大量題庫；新增自動作答；Chrome 意外關閉自動重連；支援多帳號 |
| V1.4.5 | 修正 selenium headless 模式參數問題 |
| V1.4.4 | 新增程式結束時自動清理 Chrome 行程；修正打包後路徑問題 |
| V1.4.3 | 新增精確 ChromeDriver 版本匹配；修正 exe 路徑問題 |
| V1.4.2 | 重構為模組化架構 |
| V1.4.0 | 全面重構為效能精進架構 |

---

## 使用須知

本工具僅作為公務同仁研習管理之輔助方案。使用者應於合適之時間與環境下運用，並遵守目標平台之使用規範。
