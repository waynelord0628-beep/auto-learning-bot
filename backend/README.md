# eCPA 待查維護

這些模組用於既有 GAS 專案，沿用專案原本的 GitHub、Telegram 與 AI 設定。不含部署憑證。

- `ecpa_candidate_intake.js`：取代 `handleEcpaMissing`，收件去重、候選格式檢查、不逐批推送待查通知。
- `ecpa_evidence.js`：取代原證據模組；正式答案更新與相符待查項目結案在同一個非強制 Git commit 中完成。
- `ecpa_review_worker.js`：新增維護函式。在 Apps Script 觸發條件介面建立 `processEcpaReviewQueue` 每小時排程，建立前確認沒有同名排程。使用介面設定，不需要授予程式排程管理權限。

每次最多掃描 10 門課，持續輪替。每輪最多重試 10 個缺少候選的項目，每題一天最多重試一次、最多三次。已有候選時不再次呼叫 AI。重試只產生未驗證候選，不會寫入正式題庫。無變更時不寫 commit、不推送 Telegram。

`status=pending` 保留相容性供已發布的 2.1.9 讀取；`review_state` 區分可用候選、等待正解、資料不足及其他狀態。只有同課程、同題型、同題目、同组选項且具平台證據的正式答案可令項目自動結案。AI 一致或已有舊題庫答案都不算新的正解證據。

2.1.9 雲端候選匯入只接受單一答案。複選候選標記 `awaiting_multiselect_support` 並保留原始候選，不反覆呼叫 AI，也不把複選答案降成單選。

驗證：`node backend/test_ecpa_review.cjs`、`node backend/test_ecpa_evidence.cjs`。

部署需要在既有 Apps Script 編輯器儲存上述函式，更新原 Web App 部署版本（保留既有 URL），再於觸發條件介面設定每小時執行 `processEcpaReviewQueue`。單純推送 GitHub 不會更新 GAS 或啟用排程。
