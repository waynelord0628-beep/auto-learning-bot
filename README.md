<div align="center">

# 行政效能領航員

**讓繁瑣流程退後，讓研習回到眼前。**

整合 eCPA、我的E政府與臺北E大，從課程研習到完成確認，少一點來回操作。

![版本](https://img.shields.io/badge/版本-V2.1.14-233C53?style=flat-square)
![系統](https://img.shields.io/badge/系統-Windows-607D8B?style=flat-square)
![瀏覽器](https://img.shields.io/badge/瀏覽器-Chrome-607D8B?style=flat-square)

[**下載最新版 ↗**](https://github.com/waynelord0628-beep/auto-learning-bot/releases/latest) · [Google Drive 備援](https://drive.google.com/drive/folders/1Fm6CwmV2AsoWaUOGV0V5hZbgP_GJrU8g) · [更新紀錄](https://github.com/waynelord0628-beep/auto-learning-bot/releases)

</div>

---

## 一個入口，接好整段流程

不必反覆切換頁面、確認進度。領航員把常用操作放在一起，也把完成狀態確認清楚。

| | 領航員幫你處理 |
| :--- | :--- |
| **帳號與平台** | 多帳號管理，依選擇的平台啟動對應流程。 |
| **課程與進度** | 串接課程播放與研習紀錄，依平台回報確認是否達標。 |
| **測驗與問卷** | 題庫作答、選用 AI 補答，以及問卷填寫與完成確認。 |
| **題庫與更新** | 同步共用題庫、回報缺題，並在入口提示新版。 |

支援 **eCPA／我的E政府／臺北E大**；實際流程依平台與課程條件而定。

## 三步，準備出發

1. **準備好 Chrome** — 在 Windows 電腦安裝 Google Chrome，下載最新版 EXE，放進固定資料夾。
2. **加入你的帳號** — 開啟領航員，新增帳號並選擇研習平台。
3. **開始執行** — 選擇人員啟動流程，從畫面查看進度；需要中止時按 **Stop**。

AI 補答為選用功能，需自行設定可用的 API key。沒有 API key，也能使用題庫作答。

> **把設定留下，讓更新接手。**
> V2.1.9 以上版本重新開啟程式後，可依提示一鍵更新；原有帳號設定與本地題庫會保留。V2.1.8 及更早版本請手動下載升級，並保留原資料夾中的 `config.json` 與 `questions.db`。

## 這一站 · V2.1.14

改善臺北E大的播放與研習時數確認。時數不足時，依平台紀錄接續研習，再銜接測驗與問卷；遇到異常則停止並提示。

**完成，以平台紀錄為準。** 畫面的計時進度不等於平台已認定完成。

## 遇到狀況，留下線索

請提供 **版本、平台、發生步驟與相關 log**，方便定位問題。上課時避免同帳號多開，或手動關閉正在使用的瀏覽器；測驗連續三次未成功時，請改由本人處理。

回報前，請先遮蔽帳密、API key 與其他個人資料。

[回報問題 ↗](https://github.com/waynelord0628-beep/auto-learning-bot/issues)

<details>
<summary><strong>給想自己動手的人</strong></summary>

安裝 Python 與專案依賴後，從圖形介面啟動：

```bash
git clone https://github.com/waynelord0628-beep/auto-learning-bot.git
cd auto-learning-bot
pip install -r requirements.txt
python ui.py
```

帳號與執行選項由介面管理；請勿將自己的設定檔、題庫或金鑰提交到 Git。

</details>

---

<div align="center">

**行政效能領航員** · 少一點重複操作，多一點從容。

</div>
