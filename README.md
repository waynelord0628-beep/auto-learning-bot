# 行政效能領航員 (AdminEfficiencyPilot)

**版本 V2.1.10** | 數位研習輔助工具

[![Python Version](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

協助處理公務數位研習流程，整合課程播放、測驗作答、問卷填寫、題庫同步與缺題回報，降低重複性操作時間。

---

## V2.1.10 更新

- 支援 eCPA 新版五碼英數圖片驗證碼，使用本機辨識。
- 改善登入速度，並確認登入狀態與學習紀錄頁後才繼續執行。
- 改善網站暫時異常時的提示與登入恢復。

V2.1.9 使用者重新開啟程式並點選「立即更新」，即可自動下載、替換及重新啟動；帳號設定與本地題庫會保留。V2.1.8 及更早版本請手動下載最新版，關閉舊版後放回原資料夾使用。

## V2.1.9 更新

- 新增入口一鍵更新，下載完成後自動重新啟動。
- 改善上課播放穩定性與部分課程單元切換。
- 改善操作流暢度與啟動、停止流程。
- 改善部分題型作答相容性，減少重複補題通知。

更新方式：2.1.8 使用者請下載下方 EXE，關閉舊版後放回原資料夾使用，保留原有 config.json 與題庫。升級至 2.1.9 後，後續版本即可使用一鍵更新。

---

## V2.1.2 里程碑：臺北E大支援

V2.1.2 是一個小里程碑：除了既有 eCPA / 我的E政府流程外，正式加入 **臺北E大**。

臺北E大的流程比一般平台更複雜，課程可能只要求問卷，也可能同時要求測驗與問卷；本版新增獨立流程，會依學習狀態優先處理已達成時數但尚未完成測驗/問卷的課程，再進入尚未達時數的課程播放。

重點更新：

- 新增「臺北E大」登入與課程流程
- 支援臺北E大課程時數判斷，依完成條件計算應閱讀時間
- 支援已達時數但未測驗/未問卷的優先處理
- 支援臺北E大測驗題庫作答、缺題回報、AI 補答
- 臺北E大缺題回報走獨立 action，並同步推送 Telegram / 更新題庫
- 修正更新檢查邏輯，後續以 GitHub `version.txt` 為主要版本來源
- 修正停止或關閉程式時，瀏覽器視窗應同步關閉
- 優化題庫同步與缺題回報，避免影響正常作答流程

---

## 支援平台

| 平台 | 狀態 | 說明 |
|---|---|---|
| eCPA | 支援 | 課程播放、測驗、題庫、缺題回報 |
| 我的E政府 | 支援 | 既有課程流程支援 |
| 臺北E大 | V2.1.2 新增 | 課程時數、測驗、問卷、缺題回報、AI 補答 |

---

## 主要功能

- PySide6 圖形介面
- 多帳號設定與平台選擇
- Chrome / ChromeDriver 自動管理
- 課程播放與時數判斷
- 測驗題庫自動作答
- 題庫同步與本地 `questions.db`
- 缺題回報至 GAS / Telegram
- 第二次測驗起可啟用 AI 補答
- 問卷自動填寫
- STOP / 關閉程式時清理瀏覽器程序
- 版本檢查與更新提示

---

## 快速使用

1. 安裝 Google Chrome
2. 下載最新版 `行政效能領航員.exe`
3. 建議放在固定資料夾，例如 `D:\autoLearning\`
4. 開啟程式後新增帳號，選擇平台與登入資料
5. 按開始執行

下載位置：

- [GitHub 正式版下載](https://github.com/waynelord0628-beep/auto-learning-bot/releases/latest)
- [Google Drive 備援下載](https://drive.google.com/drive/folders/1Fm6CwmV2AsoWaUOGV0V5hZbgP_GJrU8g?usp=sharing)

---

## 開發環境

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

---

## 設定檔

`config.json` 由 GUI 建立與維護，常見欄位如下：

```json
{
  "accounts": [
    {
      "name": "使用者",
      "login_type": "ecpa",
      "account": "A123456789",
      "password": "your_password"
    }
  ],
  "settings": {
    "headless": false,
    "residence_time": 75,
    "target_percentage": 1.05,
    "ai_provider": "Gemini",
    "ai_keys": {
      "Gemini": "your-api-key"
    },
    "ai_model": "gemini-3.1-flash-lite"
  }
}
```

`login_type` 可用值：

| 值 | 平台 |
|---|---|
| `ecpa` | eCPA |
| `egov` | 我的E政府 |
| `taipei_eda` | 臺北E大 |

---

## 題庫與補題

- `questions.db`：本地 SQLite 題庫
- `patches/questions_patch.json`：eCPA 補丁題庫
- `patches/taipei_quiz_bank.json`：臺北E大題庫
- `patches/db_version.txt`：題庫版本

缺題流程：

1. 第一次測驗優先使用題庫
2. 不及格或缺題時，第二次起可啟用 AI 補答
3. 沒有 AI 補答時，會持續使用題庫嘗試
4. 測驗失敗或缺題會回報 GAS
5. GAS 推送 Telegram 並補進 JSON / DB

---

## 打包

最小基準沿用舊版打包方式：

```cmd
uv run pyinstaller --noconfirm "行政效能領航員_V2.1.10.spec"
```

V2.1.2 因新增臺北E大，需要額外帶入：

- `ddddocr`
- `onnxruntime`
- `opencv-python` / `cv2`
- `drivers`
- `patches`
- `questions.db`
- `db_version.txt`
- `version.txt`

目前建置設定為 `行政效能領航員_V2.1.10.spec`，輸出位於 `dist/`。爬蟲的 `scrapers/*_progress.json` 是本機續跑檔，不再提交到 Git；既有本機檔案可以繼續使用。

---

## 更新檢查

V2.1.9 起，程式會在入口畫面檢查 GitHub 最新正式 Release，只有線上版本較新時才提示更新。進入執行流程後不會再突然跳出更新視窗；本次啟動的檢查結束後才發布的版本，請下次重新開啟程式取得。

點選「立即更新」後，程式會下載並核對 EXE 的大小與 SHA-256，再備份舊版、替換及重新啟動。若新版啟動失敗，更新程序會嘗試還原舊版；帳號設定與題庫不會被更新程序覆蓋。

發布者需同步提高程式內版本號與 Release 標籤（例如 `V2.1.10`），發布為正式版，並附上**單一 EXE**；下載檔必須有 GitHub 提供的 SHA-256 資訊。草稿、預發布版本或覆蓋同版本不會觸發一鍵更新。

V2.1.8 及更早版本仍使用 `main/version.txt` 的舊版提示流程，請手動下載升級。Google Drive 為備援下載位置，不是一鍵更新的檢查來源。

---

## 更新紀錄

| 版本 | 內容 |
|---|---|
| V2.1.10 | 支援 eCPA 五碼圖片驗證碼，改善登入速度、狀態確認與網站異常時的登入恢復。 |
| V2.1.9 | 新增入口一鍵更新與自動重新啟動，改善播放、操作及缺題通知穩定性。 |
| V2.1.8 | 僅處理開放式課程，自動略過微學習/SPOC/專班等非開放式課程；課程清單低筆數時會同步 session 後重抓，降低漏課機率；同步調整帳號列表捲動與下拉箭頭顯示。 |
| V2.1.7 | 修補 eCPA 多頁測驗/問卷流程，讓已達時數但仍需測驗或問卷的課程可繼續處理；改善課程 API 回傳異常時的重抓與續跑穩定性。 |
| V2.1.6 | 新增匿名使用統計與在線人數顯示；更新下載恢復優先使用 GitHub Release；包含臺北E大題幹污染清理 |
| V2.1.5 | 修正重登後誤把 PDF / 教材分頁當成教室主視窗，導致連續找不到 `s_catalog` 並重啟的問題；改為自動掃描含 `s_catalog/pathtree` 的正確教室視窗 |
| V2.1.4 | 修正 eCPA / 我的E政府課程中途閒置登出後，API 回傳 0 筆造成時數判斷錯誤與流程卡死；改為自動重新登入、同步 session，必要時重啟輔助引擎 |
| V2.1.3 | 修正 4 選 1 單選題答案映射，AI / 題庫答案可正確比對 radio 選項並點選；加強去標點與 normalized 比對 |
| V2.1.2 | 新增臺北E大平台流程，支援時數判斷、測驗、問卷、缺題回報與 AI 補答；更新檢查改以 GitHub `version.txt` 為主 |
| V2.1.1 | 修正題庫同步與缺題流程，優化 Gemini 模型設定與回報穩定性 |
| V2.1.0 | Gemini 模型更新，更新提示導向 Google Drive |
| V2.0.9 | 優化題庫與 AI 補答流程 |
| V2.0.8 | 修正 UI 與更新流程 |
| V2.0.6 | 修正 alert、session 與 ChromeDriver 流程 |
| V2.0.0 | 建立 PySide6 GUI 版本 |

---

## 注意事項

- 請勿在課程進行中手動關閉 Chrome，除非要停止流程
- 若 ChromeDriver 異常，先重新開啟程式或更新 Chrome
- 若題庫缺題，程式會依流程回報，不需要手動修改 DB
- 使用 AI 補答需在設定中填入可用 API key
