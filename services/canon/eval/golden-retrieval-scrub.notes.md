# 結果檔不含題庫題目文字（2026-10-24）

題庫是外部的（chepb 倉庫），結果檔不複製其題目：每題只留 `id`、`bucket`、期望條文、對應條與名次，
以及題目文字的 `question_sha256`（UTF-8，sha256），要看題目用 `id` 回題庫查，並以題庫檔的 sha256 確認版本。

- 工具 `tools/golden_retrieval_eval.py` 自此版本起不輸出 `question`，也只記題庫檔名、不記本機絕對路徑。
- `golden-retrieval-2026-10-23`、`golden-retrieval-scoped-2026-10-24`、`golden-retrieval-scoped2-2026-10-24` 三份是事後處理：
  只做兩件事——把每題的 `question` 換成 `question_sha256`、把 `meta.golden.path` 與 `.md` 內的題庫路徑改成檔名。
  `summary`、名次、分數、範圍與嵌入設定都未動，所以各表數字與原先相同。
- 這三份在處理前由工具直接產生的版本，保留在本分支較早的提交裡（含題目文字）；若要不留在歷史中，合併時請 squash。
