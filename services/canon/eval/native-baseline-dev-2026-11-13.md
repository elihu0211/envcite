# 黃金題庫條文檢索召回（envcite）

- 執行：2026-11-13T08:44:47+00:00（as_of 2026-11-13）；commit `4f976d33027aa27ca687c3366199f8d77e2af7ed`
- 題庫：`native-golden-2026-11-13.json`，sha256 `769ebefddc130c6b1d65f66a152c051ee96d0374c6865de1b021bae180fbef4f`，1408 題
- qrels：`native-golden-2026-11-13.qrels.json`，sha256 `b54dae369cba318a8cea6f41d5072887d707636b9eff7e3c4ec616f792e5acf0`；模型判定檔 0 份
- 切分：`native-golden-2026-11-13.split.json`，sha256 `79bc76ff26d5ecdf9ea6b376b0bfc4ff4bba653c87fc75b41bfb3322e1666b1b`；本報告為 **dev**，681 題
- 語料快照：moj_law data_date 2026-11-06；moj_order data_date 2026-11-06
- 語料：11804 部法規、911567 個條文單元、650276 筆 passage（其中有嵌入 0）
- 檢索：僅字面（未設定嵌入端點）；分詞器 `lexbigram-1+dict-94450239`；k = 50；問句點名的法規縮小範圍
- 指標由 ranx 以 qrels 與 run 算出：R@k 是 `hit_rate@k`（任一分數 ≥ 相關門檻的條在前 k 條），MRR 是 `mrr`，nDCG@10 以 0–3 分級為增益；Hole@10 是前 10 條中 qrels 沒有判定的比例，洞裡的條未經判定，Hole@10 不為 0 時召回是下界。unmapped 不計入分母，原因逐題列在 qrels；部分對應的題只以對得到的條文計。僅檢索層，不含答對率。

## lexical 臂

| 桶 | 題數 | unmapped | 部分對應 | 計分 n | R@1 | R@5 | R@10 | R@20 | R@50 | MRR | nDCG@10 | Hole@10 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| exact_id | 284 | 0 | 0 | 284 | 0.116 | 0.391 | 0.592 | 0.704 | 0.845 | 0.246 | 0.310 | 93.8% |
| paraphrase | 0 | 0 | 0 | 0 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | — |
| multi_hop | 349 | 0 | 0 | 349 | 0.060 | 0.238 | 0.301 | 0.378 | 0.450 | 0.137 | 0.167 | 95.4% |
| 總計 | 633 | 0 | 0 | 633 | 0.085 | 0.306 | 0.431 | 0.524 | 0.627 | 0.186 | 0.231 | 94.5% |

negative 桶 48 題的最高分（不算召回）：

- top_lexical：n=48, min=0, p25=0.5, median=3.7, p75=3.7, max=4.7
- top_semantic：無
- top_rerank：無
