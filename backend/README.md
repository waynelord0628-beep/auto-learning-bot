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


## 2026-09-11 緊急修復（GAS 22）

前次替換函式時誤刪其後的 USAGE_PROP_KEY 宣告，造成在線心跳持續拋錯；doPost 的無限制錯誤通知放大成 Telegram 洗版。已還原原常數值，心跳錯誤僅留執行記錄，其他錯誤使用共用鎖及持久時間戳限制每小時最多一則通知。訊息不包含原始請求或憑證。

Code.template.gs 是移除四項私密設定後的完整後端測試範本，不可直接覆蓋現有部署設定。test_backend_incident.cjs 直接執行完整範本，涵蓋在線統計、心跳寫入、缺少常數、儲存失敗與大量錯誤通知上限。

驗證：node backend/test_backend_incident.cjs。正式 Web App 已驗證 usage_stats、usage_ping 與既有匿名裝置心跳寫入正常。未修改或重新發布 2.1.9 EXE。


## 2026-09-11 自動來源查證（GAS 23）

新增 ecpa_auto_verify.js；原排程函式保留 processEcpaReviewQueue 名稱，先做既有維護，再主動查證缺題。以上舊段落所述「只維護、不查證」由本節更新。

每輪最多 3 題、每日最多 48 題（UTC），API 呼叫前持久保留額度並取得有期限的工作租約。查證期間不持有 Git 寫入鎖，避免阻擋使用者上傳或在線心跳。未通過者 7 天後重試、最多 3 次；複選、選項不足、考試失敗及已有正式答案衝突不自動覆寫。

查證使用 Responses web_search（gpt-4.1），只搜尋指定官方來源。後端再次 HTTPS 讀取來源，拒絕跳轉，核對連續原文存在，再以另一個不提供候選答案的請求審核原文與題目。這是有來源的 AI 審核，不等於平台公布答案，也不保證答案絕對正確。原文不足、來源不可讀、兩次判斷不同均不入庫。

通過者以 official_source_reviewed 儲存來源 URL、短引文、模型與時間；題庫、版本及待查結案同一個非強制 commit。已發布項目重跑不再次發布。每輪正式更新才合併推送一則 Telegram；來源紀錄保存在題庫中，訊息清楚區分平台答案。服務錯誤保留在題目及執行失敗紀錄。Telegram 送出結果記於 ECPA_VERIFY_LAST_NOTIFICATION；網路逾時可能無法確認送達，為避免重複通知不盲目重送。

測試：node backend/test_ecpa_auto_verify.cjs。另須跑既有 review、evidence、backend_incident 測試。官方 API 參考：https://developers.openai.com/api/docs/guides/tools-web-search


## 2026-09-11 課程解答優先（GAS 24）

先用 source_index 找原 DB 的公開 source_url，直接重讀 Peigogo、Rodiyer、roddayeye 痞客邦課程頁；只索引網址、不輸出 DB 答案或帳密。索引依題目與課程雜湊切成 256 個小檔，避免單檔超出 GitHub Contents API 限制。建置指令：python backend/build_course_source_index.py <questions.db> review/source_index。

頁面標題須符合課程，题目文字與完整组选項須相符，並解析頁面 V/✓ 正解標記。找到確切解答不呼叫 AI，來源答案不同不發布；原 DB 答案不作為核實依據。找不到時才搜尋這三個網站的新解答頁，仍須讀取原文解析，最後才用原官方來源查證作備援。非官方解答以 course_answer_page / course_answer_exact_v1 記錄，明確與平台答案區別。這些網站也可能有錯，不能保證及格。

前一版因官方來源不足而待查的題目可立即進新流程一次，後續維持原重試間隔與每日額度；客戶端考三次不及格就跳過的規則沒有修改。已存在正式答案不在本次擴大覆寫。

驗證：node backend/test_ecpa_course_sources.cjs，加上既有 auto_verify/review/evidence/backend_incident 測試。已用實際下載的痞客邦「低碳蔬食活力GO」頁面與 DB 網址索引驗證是非答案對應。
