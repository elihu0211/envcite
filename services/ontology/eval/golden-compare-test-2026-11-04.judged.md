# 黃金題庫檢索比較（envcite）

- qrels sha256 `78142a093ec1b18e88b26819ceabe57369e160b82a7594d8278361a80c9359bc`；切分 sha256 `2e4dfd1335c7002d84f7ac51bce73685e13b61470705a12f4cafa417afbf055e`，子集 **test**，171 題
- 檢定：Fisher 隨機化檢定（ranx compare，成對、雙尾，10000 次置換，種子 0）；α = 0.05。未通過檢定的差距不寫成改善或退步；Hole@10 偏高時，未判定的條可能改變結論。

| run | R@1 | R@5 | R@10 | R@20 | R@50 | MRR | nDCG@10 | Hole@10 |
|---|---|---|---|---|---|---|---|---|
| golden-retrieval-test-2026-11-04.lexical | 0.059 | 0.164 | 0.269 | 0.357 | 0.444 | 0.116 | 0.222 | 0.1% |
| golden-retrieval-test-2026-11-04.hybrid | 0.228 | 0.386 | 0.468 | 0.509 | 0.550 | 0.306 | 0.485 | 0.0% |

| a → b | 指標 | 差（b − a） | p | 結論 |
|---|---|---|---|---|
| golden-retrieval-test-2026-11-04.lexical → golden-retrieval-test-2026-11-04.hybrid | R@1 | +0.1696 | 0.0000 | 差異顯著 |
| golden-retrieval-test-2026-11-04.lexical → golden-retrieval-test-2026-11-04.hybrid | R@5 | +0.2222 | 0.0000 | 差異顯著 |
| golden-retrieval-test-2026-11-04.lexical → golden-retrieval-test-2026-11-04.hybrid | R@10 | +0.1988 | 0.0000 | 差異顯著 |
| golden-retrieval-test-2026-11-04.lexical → golden-retrieval-test-2026-11-04.hybrid | R@20 | +0.1520 | 0.0000 | 差異顯著 |
| golden-retrieval-test-2026-11-04.lexical → golden-retrieval-test-2026-11-04.hybrid | R@50 | +0.1053 | 0.0000 | 差異顯著 |
| golden-retrieval-test-2026-11-04.lexical → golden-retrieval-test-2026-11-04.hybrid | MRR | +0.1902 | 0.0000 | 差異顯著 |
| golden-retrieval-test-2026-11-04.lexical → golden-retrieval-test-2026-11-04.hybrid | nDCG@10 | +0.2625 | 0.0000 | 差異顯著 |
