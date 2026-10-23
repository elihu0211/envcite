# 黃金題庫條文檢索召回（envcite）

- 執行：2026-10-23T16:01:04+00:00（as_of 2026-10-23）；commit `88845f94cbc66bdc070cb0e4bb85e53dcffd7d84`
- 題庫：`/Users/elihu/Documents/GitLab/chepb-envkm/chepb-1150011621/backend/src/chepb/cli/golden_sets/regulations-golden-eval.yaml`，sha256 `b6ab24712269a48d111eb41ac8b38f354d97848a2947303d3ca03fde7b8c897f`，471 題
- 語料快照：moj_law data_date 2026-10-16；moj_order data_date 2026-10-16
- 語料：11802 部法規、911473 個條文單元、650189 筆 passage（其中有嵌入 0）
- 檢索：僅字面（未設定嵌入端點）；分詞器 `lexbigram-1+dict-51fb8e24`；k = 50
- 命中：任一期望條文在前 k 條；unmapped 不計入分母，原因逐題列在 JSON；部分對應的題只以對得到的條文計。僅檢索層，不含答對率。

## lexical 臂

| 桶 | 題數 | unmapped | 部分對應 | 計分 n | R@1 | R@5 | R@10 | R@20 | R@50 | MRR |
|---|---|---|---|---|---|---|---|---|---|---|
| exact_id | 91 | 3 | 0 | 88 | 0.000 | 0.023 | 0.045 | 0.091 | 0.204 | 0.018 |
| paraphrase | 191 | 15 | 0 | 176 | 0.000 | 0.000 | 0.011 | 0.028 | 0.074 | 0.004 |
| multi_hop | 95 | 18 | 0 | 77 | 0.000 | 0.000 | 0.026 | 0.039 | 0.091 | 0.006 |
| 總計 | 377 | 36 | 0 | 341 | 0.000 | 0.006 | 0.024 | 0.047 | 0.111 | 0.008 |

negative 桶 94 題的最高分（不算召回）：

- top_lexical：n=94, min=0.1, p25=1.3, median=2.6, p75=3.7, max=8.1
- top_semantic：無
