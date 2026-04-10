# 行政效能領航員 (AdminEfficiencyPilot)

**版本 V2.0.0** ｜ 數位研習輔助方案

[![Python Version](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

> 協助公務同仁自動完成「公務人員 e 等學習網」的數位研習時數，讓您專注於真正重要的工作。

---

## 目錄

- [功能說明](#功能說明)
- [執行前的準備](#執行前的準備)
- [安裝步驟（第一次使用）](#安裝步驟第一次使用)
- [每次執行的方式](#每次執行的方式)
- [設定檔說明](#設定檔說明)
- [封裝成 .exe 發給同仁](#封裝成-exe-發給同仁)
- [常見問題 FAQ](#常見問題-faq)
- [版本更新記錄](#版本更新記錄)

---

## 功能說明

本工具會自動開啟 Chrome 瀏覽器，登入「公務人員 e 等學習網」，並逐一完成各課程的研習時數，全程無需人工介入。

| 功能 | 說明 |
|------|------|
| 自動登入 | 支援 eCPA 人事服務網帳號 |
| 自動作答 | 內建 **87,707 題**題庫（三大來源），命中率 90%+ |
| 自動研習 | 瀏覽所有未達標課程直到時數足夠 |
| 即時進度 | 顯示每門課的研習百分比與剩餘時間 |
| 安全清理 | 結束後自動清理瀏覽器，不影響原本開著的 Chrome |
| 自動恢復 | Chrome 意外關閉時，自動重建連線繼續執行 |

---

## 執行前的準備

請確認您的電腦已安裝以下項目（只需確認一次）：

### 1. 確認有沒有安裝 Python

打開「命令提示字元」（按 `Win + R`，輸入 `cmd`，按 Enter），輸入：

```
python --version
```

如果出現 `Python 3.x.x`，代表已安裝。
如果出現錯誤，請前往 [python.org](https://www.python.org/downloads/) 下載安裝（版本需為 3.11 以上）。

> 安裝 Python 時，記得勾選「**Add Python to PATH**」選項！

### 2. 確認有沒有安裝 Google Chrome

前往 [chrome.google.com](https://www.google.com/chrome/) 下載安裝即可。

### 3. 安裝 uv（套件管理工具）

在命令提示字元輸入以下指令後按 Enter：

```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

安裝完成後，**關閉並重新開啟**命令提示字元，然後輸入：

```
uv --version
```

如果出現版本號，代表安裝成功。

---

## 安裝步驟（第一次使用）

### 步驟一：下載本工具

將整個專案資料夾下載到您電腦的任意位置，例如：

```
D:\autoLearning\
```

資料夾結構如下：

```
autoLearning/
├── app.py              ← 主程式
├── ui.py               ← 圖形介面
├── config.json         ← 帳號與設定
├── questions.db        ← 題庫（87,707 題）
├── dedup.py            ← 題庫去重工具
├── run.bat             ← Windows 一鍵啟動
├── requirements.txt    ← 套件清單
├── drivers/            ← 首次執行時自動建立，無需手動放置
├── icons/              ← UI 圖示
├── scrapers/           ← 題庫爬蟲（三大來源）
├── tools/
│   └── run_full_test.py
├── utils/
│   ├── helpers.py
│   └── webdriver_mgr.py
└── README.md
```

### 步驟二：開啟命令提示字元並切換到資料夾

```cmd
cd D:\autoLearning
```

（請把路徑換成您實際存放的位置）

### 步驟三：第一次執行

```cmd
uv run app.py
```

程式啟動後會開啟圖形介面，依提示輸入 eCPA 帳號密碼即可。

---

## 每次執行的方式

有三種執行方式，**擇一使用即可**：

### 方式一：雙擊執行（最簡單）

直接雙擊資料夾裡的 `run.bat` 檔案即可。

### 方式二：uv run（推薦，自動管理套件）

```cmd
cd D:\autoLearning
uv run app.py
```

### 方式三：一般 Python（需先安裝套件）

第一次使用前，先安裝所需套件：

```cmd
pip install -r requirements.txt
```

之後每次執行：

```cmd
python app.py
```

### 執行中的畫面說明

```
============================================================
【行政效能領航員 - 數位研習輔助方案 V2.0.0】
============================================================
[INFO] 無殘留 driver 行程。
[INFO] 偵測到本機 Chrome 版本: 1xx.0.x.x
[INFO] 正在下載驅動程式...          ← 自動下載對應版本的 ChromeDriver
[INFO] 正在啟動輔助引擎...
[INFO] 引擎就緒
[INFO] 正在對接 eCPA 登入系統...
[INFO] 系統身分驗證成功！
[INFO] [1/5] 正在協助研習：課程名稱
[INFO]    研習進度：00:15:00 / 00:30:00 ████████░░░░  ← 即時進度
[INFO]    時數已達標！
[INFO] 任務圓滿達成！
```

### 中途停止

直接按 `Ctrl + C` 即可安全停止，程式會自動清理瀏覽器。

---

## 設定檔說明

開啟 `config.json`，可以用記事本修改：

```json
{
    "accounts": [
        {
            "name": "王小明",
            "login_type": "eCPA",
            "account": "A123456789",
            "password": "your_password"
        }
    ],
    "settings": {
        "headless": false,
        "residence_time": 75,
        "target_percentage": 1.05
    }
}
```

**欄位說明：**

| 欄位 | 說明 |
|------|------|
| `name` | 顯示名稱（自訂） |
| `login_type` | 登入類型，固定填 `eCPA` |
| `account` | eCPA 帳號（身分證字號） |
| `password` | eCPA 密碼 |
| `headless` | `false` = 看得到瀏覽器視窗；`true` = 背景執行不開視窗 |
| `residence_time` | 每個單元停留的秒數（建議不要低於 60） |
| `target_percentage` | 每門課要完成的比例（`1.0` = 100%，`1.05` = 超過門檻一點點確保通過） |

**常用調整：**

| 情況 | 修改方式 |
|------|----------|
| 想看到瀏覽器運作過程 | `"headless": false` |
| 想完成 100% 時數 | `"target_percentage": 1.0` |
| 單元停留太短被系統偵測 | 調高 `"residence_time"` 到 90 或 120 |
| 密碼變更後 | 直接修改 `"password"` 欄位 |
| 多帳號同時執行 | 在 `accounts` 陣列新增多筆帳號 |

---

## 封裝成 .exe 發給同仁

如果要給沒有安裝 Python 環境的同仁使用，可以打包成單一 `.exe` 執行檔：

### 步驟一：安裝 PyInstaller

```cmd
uv run pip install pyinstaller
```

### 步驟二：執行打包指令

```cmd
uv run pyinstaller --onefile --name "行政效能領航員" --collect-all selenium --collect-all utils app.py
```

打包完成後，`dist/` 資料夾內會出現 `行政效能領航員.exe`。

### 步驟三：發佈給同仁

將 `dist/` 資料夾內的 **`行政效能領航員.exe`** 以及 **`questions.db`** 一起傳給同仁。

同仁第一次執行時，程式會詢問帳號密碼，並在 `.exe` 同一個資料夾建立 `config.json` 和 `drivers/` 資料夾。

> 同仁的電腦仍需安裝 Google Chrome，程式才能正常運作。

---

## 常見問題 FAQ

### 出現「引擎初始化失敗」怎麼辦？

請先確認：
1. 電腦有安裝 Google Chrome
2. 刪除專案資料夾內的 `drivers/` 資料夾，重新執行

### 登入失敗怎麼辦？

1. 用瀏覽器手動登入 eCPA 確認帳號密碼正確
2. 開啟 `config.json`，檢查帳號密碼是否正確
3. 若密碼有特殊符號，確認 JSON 格式正確

### 程式跑完但時數沒有更新？

e 等學習網的時數有時需要數分鐘才會同步，稍候重新整理頁面確認。

### 想在背景執行不顯示視窗？

確認 `config.json` 中 `"headless": true`。

### 出現「No module named ...」錯誤？

執行以下指令重新安裝套件：

```cmd
uv sync
```

### Chrome 被關掉後程式卡住怎麼辦？

V2.0.0 已內建自動偵測與重連機制，程式會自動重建 Chrome 連線並繼續執行，無需手動重啟。

### 題庫沒有這題的答案怎麼辦？

程式會略過不確定的題目，不會亂作答。題庫持續由三大來源（pixnet、peigogo、rodiyer）自動更新，如需手動更新題庫，執行：

```cmd
python scrapers/pixnet_to_sqlite.py
python scrapers/peigogo_to_sqlite.py
python scrapers/rodiyer_full_scraper.py
python dedup.py
```

---

## 版本更新記錄

| 版本 | 更新內容 |
|------|----------|
| **V2.0.0** | 新增圖形介面（UI）；內建 87,707 題三大來源題庫；新增自動作答功能（命中率 90%+）；Chrome 意外關閉後自動重連；支援多帳號設定 |
| V1.4.5 | 修正 selenium headless 模式參數問題 |
| V1.4.4 | 新增程式結束時自動清理 Chrome 行程；修正打包後路徑問題 |
| V1.4.3 | 新增精確 ChromeDriver 版本匹配；修正 exe 路徑問題 |
| V1.4.2 | 重構為模組化架構，提升穩定性 |
| V1.4.0 | 全面優化，重構為效能精進架構 |

---

## 使用須知

本工具僅作為公務同仁研習管理之輔助方案。使用者應於合適之時間與環境下運用，並遵守目標平台之使用規範。
