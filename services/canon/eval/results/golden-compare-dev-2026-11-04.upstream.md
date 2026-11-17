# 黃金題庫檢索比較（envcite）

- qrels sha256 `e424dbe60ceed97a3e838f029ccd40057b08a694098a76ab547c9e43bfa28d9b`；切分 sha256 `2e4dfd1335c7002d84f7ac51bce73685e13b61470705a12f4cafa417afbf055e`，子集 **dev**，170 題
- 檢定：Fisher 隨機化檢定（ranx compare，成對、雙尾，10000 次置換，種子 0）；α = 0.05。未通過檢定的差距不寫成改善或退步；Hole@10 偏高時，未判定的條可能改變結論。

| run | R@1 | R@5 | R@10 | R@20 | R@50 | MRR | nDCG@10 | Hole@10 |
|---|---|---|---|---|---|---|---|---|
| golden-retrieval-dev-2026-11-03.lexical | 0.000 | 0.018 | 0.035 | 0.065 | 0.112 | 0.011 | 0.013 | 99.6% |
| golden-retrieval-dev-2026-11-03.hybrid | 0.123 | 0.212 | 0.259 | 0.288 | 0.353 | 0.160 | 0.171 | 97.2% |
| golden-retrieval-dev-2026-11-04.lexical | 0.035 | 0.112 | 0.159 | 0.277 | 0.324 | 0.079 | 0.086 | 98.3% |
| golden-retrieval-dev-2026-11-04.hybrid | 0.100 | 0.294 | 0.329 | 0.353 | 0.429 | 0.176 | 0.200 | 96.3% |

| a → b | 指標 | 差（b − a） | p | 結論 |
|---|---|---|---|---|
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-03.hybrid | R@1 | +0.1235 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-03.hybrid | R@5 | +0.1941 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-03.hybrid | R@10 | +0.2235 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-03.hybrid | R@20 | +0.2235 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-03.hybrid | R@50 | +0.2412 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-03.hybrid | MRR | +0.1494 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-03.hybrid | nDCG@10 | +0.1575 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-04.lexical | R@1 | +0.0353 | 0.0316 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-04.lexical | R@5 | +0.0941 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-04.lexical | R@10 | +0.1235 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-04.lexical | R@20 | +0.2118 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-04.lexical | R@50 | +0.2118 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-04.lexical | MRR | +0.0678 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-04.lexical | nDCG@10 | +0.0726 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-04.hybrid | R@1 | +0.1000 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-04.hybrid | R@5 | +0.2765 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-04.hybrid | R@10 | +0.2941 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-04.hybrid | R@20 | +0.2882 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-04.hybrid | R@50 | +0.3176 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-04.hybrid | MRR | +0.1651 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-04.hybrid | nDCG@10 | +0.1873 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.hybrid → golden-retrieval-dev-2026-11-04.lexical | R@1 | -0.0882 | 0.0010 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.hybrid → golden-retrieval-dev-2026-11-04.lexical | R@5 | -0.1000 | 0.0028 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.hybrid → golden-retrieval-dev-2026-11-04.lexical | R@10 | -0.1000 | 0.0039 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.hybrid → golden-retrieval-dev-2026-11-04.lexical | R@20 | -0.0118 | 0.8355 | 未通過檢定 |
| golden-retrieval-dev-2026-11-03.hybrid → golden-retrieval-dev-2026-11-04.lexical | R@50 | -0.0294 | 0.4537 | 未通過檢定 |
| golden-retrieval-dev-2026-11-03.hybrid → golden-retrieval-dev-2026-11-04.lexical | MRR | -0.0816 | 0.0002 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.hybrid → golden-retrieval-dev-2026-11-04.lexical | nDCG@10 | -0.0849 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.hybrid → golden-retrieval-dev-2026-11-04.hybrid | R@1 | -0.0235 | 0.3842 | 未通過檢定 |
| golden-retrieval-dev-2026-11-03.hybrid → golden-retrieval-dev-2026-11-04.hybrid | R@5 | +0.0824 | 0.0006 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.hybrid → golden-retrieval-dev-2026-11-04.hybrid | R@10 | +0.0706 | 0.0003 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.hybrid → golden-retrieval-dev-2026-11-04.hybrid | R@20 | +0.0647 | 0.0095 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.hybrid → golden-retrieval-dev-2026-11-04.hybrid | R@50 | +0.0765 | 0.0049 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.hybrid → golden-retrieval-dev-2026-11-04.hybrid | MRR | +0.0157 | 0.3446 | 未通過檢定 |
| golden-retrieval-dev-2026-11-03.hybrid → golden-retrieval-dev-2026-11-04.hybrid | nDCG@10 | +0.0298 | 0.0509 | 未通過檢定 |
| golden-retrieval-dev-2026-11-04.lexical → golden-retrieval-dev-2026-11-04.hybrid | R@1 | +0.0647 | 0.0032 | 差異顯著 |
| golden-retrieval-dev-2026-11-04.lexical → golden-retrieval-dev-2026-11-04.hybrid | R@5 | +0.1824 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-04.lexical → golden-retrieval-dev-2026-11-04.hybrid | R@10 | +0.1706 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-04.lexical → golden-retrieval-dev-2026-11-04.hybrid | R@20 | +0.0765 | 0.0003 | 差異顯著 |
| golden-retrieval-dev-2026-11-04.lexical → golden-retrieval-dev-2026-11-04.hybrid | R@50 | +0.1059 | 0.0001 | 差異顯著 |
| golden-retrieval-dev-2026-11-04.lexical → golden-retrieval-dev-2026-11-04.hybrid | MRR | +0.0973 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-04.lexical → golden-retrieval-dev-2026-11-04.hybrid | nDCG@10 | +0.1147 | 0.0000 | 差異顯著 |
