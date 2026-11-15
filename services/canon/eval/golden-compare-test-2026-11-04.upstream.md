# 黃金題庫檢索比較（envcite）

- qrels sha256 `e424dbe60ceed97a3e838f029ccd40057b08a694098a76ab547c9e43bfa28d9b`；切分 sha256 `2e4dfd1335c7002d84f7ac51bce73685e13b61470705a12f4cafa417afbf055e`，子集 **test**，171 題
- 檢定：Fisher 隨機化檢定（ranx compare，成對、雙尾，10000 次置換，種子 0）；α = 0.05。未通過檢定的差距不寫成改善或退步；Hole@10 偏高時，未判定的條可能改變結論。

| run | R@1 | R@5 | R@10 | R@20 | R@50 | MRR | nDCG@10 | Hole@10 |
|---|---|---|---|---|---|---|---|---|
| golden-retrieval-test-2026-11-04.lexical | 0.047 | 0.099 | 0.164 | 0.222 | 0.316 | 0.083 | 0.093 | 98.1% |
| golden-retrieval-test-2026-11-04.hybrid | 0.105 | 0.199 | 0.257 | 0.316 | 0.357 | 0.152 | 0.166 | 97.2% |

| a → b | 指標 | 差（b − a） | p | 結論 |
|---|---|---|---|---|
| golden-retrieval-test-2026-11-04.lexical → golden-retrieval-test-2026-11-04.hybrid | R@1 | +0.0585 | 0.0059 | 差異顯著 |
| golden-retrieval-test-2026-11-04.lexical → golden-retrieval-test-2026-11-04.hybrid | R@5 | +0.0994 | 0.0003 | 差異顯著 |
| golden-retrieval-test-2026-11-04.lexical → golden-retrieval-test-2026-11-04.hybrid | R@10 | +0.0936 | 0.0021 | 差異顯著 |
| golden-retrieval-test-2026-11-04.lexical → golden-retrieval-test-2026-11-04.hybrid | R@20 | +0.0936 | 0.0002 | 差異顯著 |
| golden-retrieval-test-2026-11-04.lexical → golden-retrieval-test-2026-11-04.hybrid | R@50 | +0.0409 | 0.0667 | 未通過檢定 |
| golden-retrieval-test-2026-11-04.lexical → golden-retrieval-test-2026-11-04.hybrid | MRR | +0.0695 | 0.0000 | 差異顯著 |
| golden-retrieval-test-2026-11-04.lexical → golden-retrieval-test-2026-11-04.hybrid | nDCG@10 | +0.0727 | 0.0000 | 差異顯著 |
