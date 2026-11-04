# 黃金題庫檢索比較（envcite）

- qrels sha256 `78142a093ec1b18e88b26819ceabe57369e160b82a7594d8278361a80c9359bc`；切分 sha256 `2e4dfd1335c7002d84f7ac51bce73685e13b61470705a12f4cafa417afbf055e`，子集 **dev**，170 題
- 檢定：Fisher 隨機化檢定（ranx compare，成對、雙尾，10000 次置換，種子 0）；α = 0.05。未通過檢定的差距不寫成改善或退步；Hole@10 偏高時，未判定的條可能改變結論。

| run | R@1 | R@5 | R@10 | R@20 | R@50 | MRR | nDCG@10 | Hole@10 |
|---|---|---|---|---|---|---|---|---|
| golden-retrieval-dev-2026-11-03.lexical | 0.000 | 0.041 | 0.071 | 0.118 | 0.176 | 0.022 | 0.085 | 0.2% |
| golden-retrieval-dev-2026-11-03.hybrid | 0.188 | 0.365 | 0.453 | 0.471 | 0.500 | 0.255 | 0.409 | 0.2% |
| golden-retrieval-dev-2026-11-04.lexical | 0.065 | 0.176 | 0.259 | 0.347 | 0.406 | 0.125 | 0.231 | 0.2% |
| golden-retrieval-dev-2026-11-04.hybrid | 0.182 | 0.447 | 0.518 | 0.541 | 0.577 | 0.284 | 0.500 | 0.1% |

| a → b | 指標 | 差（b − a） | p | 結論 |
|---|---|---|---|---|
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-03.hybrid | R@1 | +0.1882 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-03.hybrid | R@5 | +0.3235 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-03.hybrid | R@10 | +0.3824 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-03.hybrid | R@20 | +0.3529 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-03.hybrid | R@50 | +0.3235 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-03.hybrid | MRR | +0.2332 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-03.hybrid | nDCG@10 | +0.3241 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-04.lexical | R@1 | +0.0647 | 0.0013 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-04.lexical | R@5 | +0.1353 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-04.lexical | R@10 | +0.1882 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-04.lexical | R@20 | +0.2294 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-04.lexical | R@50 | +0.2294 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-04.lexical | MRR | +0.1030 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-04.lexical | nDCG@10 | +0.1458 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-04.hybrid | R@1 | +0.1824 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-04.hybrid | R@5 | +0.4059 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-04.hybrid | R@10 | +0.4471 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-04.hybrid | R@20 | +0.4235 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-04.hybrid | R@50 | +0.4000 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-04.hybrid | MRR | +0.2621 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-04.hybrid | nDCG@10 | +0.4145 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.hybrid → golden-retrieval-dev-2026-11-04.lexical | R@1 | -0.1235 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.hybrid → golden-retrieval-dev-2026-11-04.lexical | R@5 | -0.1882 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.hybrid → golden-retrieval-dev-2026-11-04.lexical | R@10 | -0.1941 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.hybrid → golden-retrieval-dev-2026-11-04.lexical | R@20 | -0.1235 | 0.0023 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.hybrid → golden-retrieval-dev-2026-11-04.lexical | R@50 | -0.0941 | 0.0225 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.hybrid → golden-retrieval-dev-2026-11-04.lexical | MRR | -0.1302 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.hybrid → golden-retrieval-dev-2026-11-04.lexical | nDCG@10 | -0.1783 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.hybrid → golden-retrieval-dev-2026-11-04.hybrid | R@1 | -0.0059 | 1.0000 | 未通過檢定 |
| golden-retrieval-dev-2026-11-03.hybrid → golden-retrieval-dev-2026-11-04.hybrid | R@5 | +0.0824 | 0.0026 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.hybrid → golden-retrieval-dev-2026-11-04.hybrid | R@10 | +0.0647 | 0.0069 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.hybrid → golden-retrieval-dev-2026-11-04.hybrid | R@20 | +0.0706 | 0.0041 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.hybrid → golden-retrieval-dev-2026-11-04.hybrid | R@50 | +0.0765 | 0.0041 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.hybrid → golden-retrieval-dev-2026-11-04.hybrid | MRR | +0.0289 | 0.1266 | 未通過檢定 |
| golden-retrieval-dev-2026-11-03.hybrid → golden-retrieval-dev-2026-11-04.hybrid | nDCG@10 | +0.0904 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-04.lexical → golden-retrieval-dev-2026-11-04.hybrid | R@1 | +0.1176 | 0.0002 | 差異顯著 |
| golden-retrieval-dev-2026-11-04.lexical → golden-retrieval-dev-2026-11-04.hybrid | R@5 | +0.2706 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-04.lexical → golden-retrieval-dev-2026-11-04.hybrid | R@10 | +0.2588 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-04.lexical → golden-retrieval-dev-2026-11-04.hybrid | R@20 | +0.1941 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-04.lexical → golden-retrieval-dev-2026-11-04.hybrid | R@50 | +0.1706 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-04.lexical → golden-retrieval-dev-2026-11-04.hybrid | MRR | +0.1590 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-04.lexical → golden-retrieval-dev-2026-11-04.hybrid | nDCG@10 | +0.2688 | 0.0000 | 差異顯著 |
