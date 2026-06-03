# 行政效能領航員 (AdminEfficiencyPilot)

**版本 V2.1.0** ｜ 數位研習輔助方案

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
| 自動作答 | 本地題庫 + AI 補答（OpenAI / Gemini / 其他相容 API） |
| 自動研習 | 瀏覽所有未達標課程直到時數足夠 |
| 自動測驗/問卷 | 自動完成課程測驗與滿意度問卷 |
| 即時進度 | 顯示每門課的研習百分比與剩餘時間 |
| 閒置登出恢復 | 偵測閒置登出 alert，自動重新登入並繼續當前課程 |
| Session 自動恢復 | Chrome 意外關閉時，自動重建連線繼續執行 |
| 防呆休眠 | 執行期間防止電腦進入睡眠 |
| 漏題自動補正 | 遇到題庫沒有的題目，自動回報並由 AI 補答，所有使用者共享更新 |
| 版本檢查 | 啟動時自動檢查是否有新版本，並導向雲端下載頁面 |

---

## 快速開始（一般使用者）

> 一般使用者建議直接使用打包好的 `.exe`，無需安裝 Python。

### 步驟一：下載執行檔

前往雲端資料夾下載最新版 `行政效能領航員.exe`：

👉 [點此前往雲端下載](https://drive.google.com/drive/folders/1Fm6CwmV2AsoWaUOGV0V5hZbgP_GJrU8g?usp=sharing)

下載後放到任意資料夾，例如 `D:\autoLearning\`。

### 步驟二：確認已安裝 Google Chrome

前往 [chrome.google.com](https://www.google.com/chrome/) 下載安裝。

### 步驟三：執行

雙擊 `行政效能領航員.exe`，依介面提示新增帳號、設定後即可開始。

第一次執行時程式會自動：
- 偵測 Chrome 版本並下載對應的 ChromeDriver 到同目錄 `drivers/`
- 在同目錄建立 `config.json` 儲存帳號與設定
- 從雲端同步最新題庫到本地 `questions.db`

### 有新版本時

程式啟動後若偵測到新版本，會自動跳出提示視窗，點擊「前往雲端下載新版本」即可前往下載頁面，下載後替換舊版 exe 即可完成更新。

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
├── patches/
│   ├── questions_patch.json  ← 雲端題庫補丁（共享）
│   └── db_version.txt        ← 題庫版本號
├── requirements.txt    ← 套件清單
├── run.bat             ← Windows 一鍵啟動
├── icons/              ← UI 圖示
└── utils/
    ├── helpers.py
    └── webdriver_mgr.py  ← Chrome / ChromeDriver 管理
```

> `config.json`、`questions.db`、`drivers/`、`build/`、`dist/`、`*.spec` 等敏感檔/建置產物不在 repo 中。

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
        "ai_provider": "OpenAI",
        "ai_keys": {
            "OpenAI": "sk-..."
        },
        "ai_base_url": "https://api.openai.com/v1",
        "ai_model": "gpt-4o-mini"
    }
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
| `ai_provider` | AI 補答引擎（`OpenAI` / `Gemini`） |
| `ai_keys` | 各 AI 的 API key |
| `ai_base_url` / `ai_model` | AI 端點與模型設定 |

---

## 自行打包 .exe

```cmd
uv run pip install pyinstaller
uv run pyinstaller 行政效能領航員_V2.0.6.spec
```

完成後 `dist/行政效能領航員.exe` 即為單檔執行版。

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

程式會自動偵測 alert、重新登入並繼續上課，若仍持續發生請回報 log。

### 題庫沒有答案怎麼辦？

程式會自動將缺題回報到雲端，由 AI（OpenAI gpt-4o-mini）批次補答後更新題庫。其他使用者下次啟動時會自動同步最新題庫，無需手動操作。

### 程式跑完但時數沒有更新？

e 等學習網時數有時需數分鐘才會同步，稍候重新整理頁面確認。

### 想在背景執行不顯示視窗？

GUI 設定中勾選 headless 模式。

### 出現「No module named ...」錯誤？

```cmd
uv sync
```

---

## 版本更新記錄

| 版本 | 更新內容 |
|------|----------|
| **V2.1.0** | Gemini 模型更新（優先 gemini-3.1-flash-lite）；更新提示永遠導向 Google Drive |
| **V2.0.9** | 缺題回報改為背景執行；缺題通知顯示使用者姓名；GAS 補答升級為 OpenAI 批次處理；更新提示改為雲端手動下載 |
| **V2.0.8** | 自動更新流程修復；首次啟動自動改名；啟動清舊版 exe；題庫靜默背景更新 |
| V2.0.6 | 修復閒置登出 alert 未被攔截導致無限重試的問題；RELOGIN 後補呼叫 `sync_session()` 確保 cookie 同步 |
| V2.0.5 | 修復核心迴圈 crash；UI 微調；log 中遮蔽 API key 與密碼 |
| V2.0.0 | 新增圖形介面（PySide6）；內建大量題庫；新增自動作答；Chrome 意外關閉自動重連；支援多帳號 |
| V1.4.x | 修正 headless 參數、ChromeDriver 版本匹配、模組化重構 |

---

## 使用須知

本工具僅作為公務同仁研習管理之輔助方案。使用者應於合適之時間與環境下運用，並遵守目標平台之使用規範。
