# 黃金題庫檢索比較（envcite）

- qrels sha256 `5647b846502ac119e22637dfa2ec6d547207633fe1a709a9f593c3d434736645`；切分 sha256 `2e4dfd1335c7002d84f7ac51bce73685e13b61470705a12f4cafa417afbf055e`，子集 **dev**，170 題
- 檢定：Fisher 隨機化檢定（ranx compare，成對、雙尾，10000 次置換，種子 0）；α = 0.05。未通過檢定的差距不寫成改善或退步；Hole@10 偏高時，未判定的條可能改變結論。

| run | R@1 | R@5 | R@10 | R@20 | R@50 | MRR | Hole@10 |
|---|---|---|---|---|---|---|---|
| golden-retrieval-dev-2026-11-03.lexical | 0.000 | 0.018 | 0.035 | 0.065 | 0.112 | 0.011 | 99.6% |
| golden-retrieval-dev-2026-11-03.hybrid | 0.123 | 0.212 | 0.259 | 0.288 | 0.353 | 0.160 | 97.2% |

| a → b | 指標 | 差（b − a） | p | 結論 |
|---|---|---|---|---|
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-03.hybrid | R@1 | +0.1235 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-03.hybrid | R@5 | +0.1941 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-03.hybrid | R@10 | +0.2235 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-03.hybrid | R@20 | +0.2235 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-03.hybrid | R@50 | +0.2412 | 0.0000 | 差異顯著 |
| golden-retrieval-dev-2026-11-03.lexical → golden-retrieval-dev-2026-11-03.hybrid | MRR | +0.1494 | 0.0000 | 差異顯著 |
