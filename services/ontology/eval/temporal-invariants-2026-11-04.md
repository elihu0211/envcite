# 版本時間軸的不變式由綱要強制（2026-11-04）

拋棄式 PostgreSQL（`pgvector/pgvector:pg18`，本機新容器），只跑 job 入口。以 main 的程式與遷移（至 0050）建庫並匯入真實資料（`envcite-sync moj`、`envcite-history moenv`、`envcite-history ly acquire` 到一半），再套用 0051、0052，用新程式重跑 `envcite-history`。

## 不變式與強制者

| 不變式 | 強制者 |
|--------|--------|
| 同一部法規同一語文的版本文字時間不重疊 | `legal_expression` 的 `UNIQUE … WITHOUT OVERLAPS`（既有） |
| 版本不留縫：除最新一版，每一版的文字時間止於同一部法規的另一版的版本日 | 0052：生成欄位 `text_until = upper(text_period)` ＋延遲外鍵到 `(resource_id, language, version_date)` |
| 同一單元現行列的效力區間不重疊 | `provision_version_current_no_overlap`（既有） |
| 施行規則與起點：`unchanged` 自版本日、`promulgation` 自版本日起算第三日、`specified` 不早於版本日、`retroactive` 早於版本日、`by_order`／`unconfirmed` 沒有區間 | 0051 新增規則值 `retroactive`；0052 `provision_version_start_follows_rule`（CHECK，只約束現行列）。條文單元版本的 `expression_date` 由觸發器自版本列帶入，複合外鍵保證與版本一致 |
| 缺版是可查詢的狀態：查不到全文的版本仍是一版，標題可不知道 | 0052：`title` 可為空，`legal_expression_title_known` 要求空標題必須 `text_status = 'unavailable'`；讀取端（`GetLaw`、詞典、法名表）只看 `available` |

## 程式的對應改動

- `ingest/law.py`：明文早於版本日的特定施行日記為 `retroactive`（不再是 `specified`）；`PARSER_VERSION` 3。
- `ingest/backfill.py`：回推停下處與更早的每一次公布寫成 `unavailable` 版本，時間軸接到制定；來源後來補上時，同一版改為 `available`。「停在那裡就不寫」的特例拿掉。
- `ingest/history.py`：法律的沿革裡「令發布…施行」是施行命令不是修正（MR !331 列為後續）。

## 實測

- **遷移套用在真實資料上**：0051、0052 共 48 秒、無違反。現行列 `specified` 97,013 單元中 24,207 個（早於版本日者）轉為 `retroactive`，與 MR !327 量到的 24,207 相符。
- 重跑 `envcite-history moenv import`：443 部全部 unchanged，0 列變動。
- 之後的資料（命令 877＋立法院法律 666 部，ly 取得只跑到約一半）：`available` 版本 16,559、`unavailable` 版本 404（來源 73 部有缺版）；相鄰版本 5,160 對，最新一版 11,803（= 法規數）；`retroactive` 25,960 單元。
- 缺版後來補上：同一版由 `unavailable` 改為 `available`（`tests/test_backfill.py`）。
- 全部測試 920 passed；新增的綱要測試對每條規則各有合法與違法的一例。
