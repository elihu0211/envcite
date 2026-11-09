# 黃金題庫檢索比較（envcite）

- qrels sha256 `66472a1daac640b8a64867bfc04d7dc71f43a4d014e9d486f58ebbf6eefd8c6b`；切分 sha256 `2e4dfd1335c7002d84f7ac51bce73685e13b61470705a12f4cafa417afbf055e`，子集 **test**，171 題
- 檢定：Fisher 隨機化檢定（ranx compare，成對、雙尾，10000 次置換，種子 0）；α = 0.05。未通過檢定的差距不寫成改善或退步；Hole@10 偏高時，未判定的條可能改變結論。

| run | R@1 | R@5 | R@10 | R@20 | R@50 | MRR | nDCG@10 | Hole@10 |
|---|---|---|---|---|---|---|---|---|
| P50-k60.r1 | 0.199 | 0.345 | 0.415 | 0.491 | 0.538 | 0.271 | 0.430 | 0.3% |
| golden-retrieval-test-2026-11-09.hybrid | 0.199 | 0.404 | 0.491 | 0.561 | 0.620 | 0.303 | 0.441 | 0.6% |

| a → b | 指標 | 差（b − a） | p | 結論 |
|---|---|---|---|---|
| P50-k60.r1 → golden-retrieval-test-2026-11-09.hybrid | R@1 | +0.0000 | 1.0000 | 未通過檢定 |
| P50-k60.r1 → golden-retrieval-test-2026-11-09.hybrid | R@5 | +0.0585 | 0.0410 | 差異顯著 |
| P50-k60.r1 → golden-retrieval-test-2026-11-09.hybrid | R@10 | +0.0760 | 0.0117 | 差異顯著 |
| P50-k60.r1 → golden-retrieval-test-2026-11-09.hybrid | R@20 | +0.0702 | 0.0012 | 差異顯著 |
| P50-k60.r1 → golden-retrieval-test-2026-11-09.hybrid | R@50 | +0.0819 | 0.0000 | 差異顯著 |
| P50-k60.r1 → golden-retrieval-test-2026-11-09.hybrid | MRR | +0.0325 | 0.0022 | 差異顯著 |
| P50-k60.r1 → golden-retrieval-test-2026-11-09.hybrid | nDCG@10 | +0.0111 | 0.2377 | 未通過檢定 |
