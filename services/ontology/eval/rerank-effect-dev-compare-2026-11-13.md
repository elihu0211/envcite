# 黃金題庫檢索比較（envcite）

- qrels sha256 `b54dae369cba318a8cea6f41d5072887d707636b9eff7e3c4ec616f792e5acf0`；切分 sha256 `79bc76ff26d5ecdf9ea6b376b0bfc4ff4bba653c87fc75b41bfb3322e1666b1b`，子集 **dev**，633 題
- 檢定：Fisher 隨機化檢定（ranx compare，成對、雙尾，10000 次置換，種子 0）；α = 0.05。未通過檢定的差距不寫成改善或退步；Hole@10 偏高時，未判定的條可能改變結論。

| run | R@1 | R@5 | R@10 | R@20 | R@50 | MRR | nDCG@10 | Hole@10 |
|---|---|---|---|---|---|---|---|---|
| rerank-effect-dev-2026-11-13.lexical | 0.085 | 0.306 | 0.431 | 0.524 | 0.627 | 0.186 | 0.231 | 94.6% |
| rerank-effect-dev-2026-11-13.lexical_rerank_d10 | 0.280 | 0.404 | 0.431 | 0.524 | 0.627 | 0.337 | 0.345 | 94.6% |
| rerank-effect-dev-2026-11-13.lexical_rerank_d20 | 0.313 | 0.458 | 0.504 | 0.524 | 0.627 | 0.379 | 0.399 | 93.6% |
| rerank-effect-dev-2026-11-13.lexical_rerank_d30 | 0.340 | 0.496 | 0.542 | 0.569 | 0.627 | 0.408 | 0.432 | 93.1% |

| a → b | 指標 | 差（b − a） | p | 結論 |
|---|---|---|---|---|
| rerank-effect-dev-2026-11-13.lexical → rerank-effect-dev-2026-11-13.lexical_rerank_d10 | R@1 | +0.1943 | 0.0000 | 差異顯著 |
| rerank-effect-dev-2026-11-13.lexical → rerank-effect-dev-2026-11-13.lexical_rerank_d10 | R@5 | +0.0979 | 0.0000 | 差異顯著 |
| rerank-effect-dev-2026-11-13.lexical → rerank-effect-dev-2026-11-13.lexical_rerank_d10 | R@10 | +0.0000 | 1.0000 | 未通過檢定 |
| rerank-effect-dev-2026-11-13.lexical → rerank-effect-dev-2026-11-13.lexical_rerank_d10 | R@20 | +0.0000 | 1.0000 | 未通過檢定 |
| rerank-effect-dev-2026-11-13.lexical → rerank-effect-dev-2026-11-13.lexical_rerank_d10 | R@50 | +0.0000 | 1.0000 | 未通過檢定 |
| rerank-effect-dev-2026-11-13.lexical → rerank-effect-dev-2026-11-13.lexical_rerank_d10 | MRR | +0.1514 | 0.0000 | 差異顯著 |
| rerank-effect-dev-2026-11-13.lexical → rerank-effect-dev-2026-11-13.lexical_rerank_d10 | nDCG@10 | +0.1138 | 0.0000 | 差異顯著 |
| rerank-effect-dev-2026-11-13.lexical → rerank-effect-dev-2026-11-13.lexical_rerank_d20 | R@1 | +0.2275 | 0.0000 | 差異顯著 |
| rerank-effect-dev-2026-11-13.lexical → rerank-effect-dev-2026-11-13.lexical_rerank_d20 | R@5 | +0.1517 | 0.0000 | 差異顯著 |
| rerank-effect-dev-2026-11-13.lexical → rerank-effect-dev-2026-11-13.lexical_rerank_d20 | R@10 | +0.0727 | 0.0000 | 差異顯著 |
| rerank-effect-dev-2026-11-13.lexical → rerank-effect-dev-2026-11-13.lexical_rerank_d20 | R@20 | +0.0000 | 1.0000 | 未通過檢定 |
| rerank-effect-dev-2026-11-13.lexical → rerank-effect-dev-2026-11-13.lexical_rerank_d20 | R@50 | +0.0000 | 1.0000 | 未通過檢定 |
| rerank-effect-dev-2026-11-13.lexical → rerank-effect-dev-2026-11-13.lexical_rerank_d20 | MRR | +0.1930 | 0.0000 | 差異顯著 |
| rerank-effect-dev-2026-11-13.lexical → rerank-effect-dev-2026-11-13.lexical_rerank_d20 | nDCG@10 | +0.1677 | 0.0000 | 差異顯著 |
| rerank-effect-dev-2026-11-13.lexical → rerank-effect-dev-2026-11-13.lexical_rerank_d30 | R@1 | +0.2543 | 0.0000 | 差異顯著 |
| rerank-effect-dev-2026-11-13.lexical → rerank-effect-dev-2026-11-13.lexical_rerank_d30 | R@5 | +0.1896 | 0.0000 | 差異顯著 |
| rerank-effect-dev-2026-11-13.lexical → rerank-effect-dev-2026-11-13.lexical_rerank_d30 | R@10 | +0.1106 | 0.0000 | 差異顯著 |
| rerank-effect-dev-2026-11-13.lexical → rerank-effect-dev-2026-11-13.lexical_rerank_d30 | R@20 | +0.0442 | 0.0000 | 差異顯著 |
| rerank-effect-dev-2026-11-13.lexical → rerank-effect-dev-2026-11-13.lexical_rerank_d30 | R@50 | +0.0000 | 1.0000 | 未通過檢定 |
| rerank-effect-dev-2026-11-13.lexical → rerank-effect-dev-2026-11-13.lexical_rerank_d30 | MRR | +0.2224 | 0.0000 | 差異顯著 |
| rerank-effect-dev-2026-11-13.lexical → rerank-effect-dev-2026-11-13.lexical_rerank_d30 | nDCG@10 | +0.2016 | 0.0000 | 差異顯著 |
| rerank-effect-dev-2026-11-13.lexical_rerank_d10 → rerank-effect-dev-2026-11-13.lexical_rerank_d20 | R@1 | +0.0332 | 0.0035 | 差異顯著 |
| rerank-effect-dev-2026-11-13.lexical_rerank_d10 → rerank-effect-dev-2026-11-13.lexical_rerank_d20 | R@5 | +0.0537 | 0.0000 | 差異顯著 |
| rerank-effect-dev-2026-11-13.lexical_rerank_d10 → rerank-effect-dev-2026-11-13.lexical_rerank_d20 | R@10 | +0.0727 | 0.0000 | 差異顯著 |
| rerank-effect-dev-2026-11-13.lexical_rerank_d10 → rerank-effect-dev-2026-11-13.lexical_rerank_d20 | R@20 | +0.0000 | 1.0000 | 未通過檢定 |
| rerank-effect-dev-2026-11-13.lexical_rerank_d10 → rerank-effect-dev-2026-11-13.lexical_rerank_d20 | R@50 | +0.0000 | 1.0000 | 未通過檢定 |
| rerank-effect-dev-2026-11-13.lexical_rerank_d10 → rerank-effect-dev-2026-11-13.lexical_rerank_d20 | MRR | +0.0416 | 0.0000 | 差異顯著 |
| rerank-effect-dev-2026-11-13.lexical_rerank_d10 → rerank-effect-dev-2026-11-13.lexical_rerank_d20 | nDCG@10 | +0.0539 | 0.0000 | 差異顯著 |
| rerank-effect-dev-2026-11-13.lexical_rerank_d10 → rerank-effect-dev-2026-11-13.lexical_rerank_d30 | R@1 | +0.0600 | 0.0000 | 差異顯著 |
| rerank-effect-dev-2026-11-13.lexical_rerank_d10 → rerank-effect-dev-2026-11-13.lexical_rerank_d30 | R@5 | +0.0916 | 0.0000 | 差異顯著 |
| rerank-effect-dev-2026-11-13.lexical_rerank_d10 → rerank-effect-dev-2026-11-13.lexical_rerank_d30 | R@10 | +0.1106 | 0.0000 | 差異顯著 |
| rerank-effect-dev-2026-11-13.lexical_rerank_d10 → rerank-effect-dev-2026-11-13.lexical_rerank_d30 | R@20 | +0.0442 | 0.0000 | 差異顯著 |
| rerank-effect-dev-2026-11-13.lexical_rerank_d10 → rerank-effect-dev-2026-11-13.lexical_rerank_d30 | R@50 | +0.0000 | 1.0000 | 未通過檢定 |
| rerank-effect-dev-2026-11-13.lexical_rerank_d10 → rerank-effect-dev-2026-11-13.lexical_rerank_d30 | MRR | +0.0710 | 0.0000 | 差異顯著 |
| rerank-effect-dev-2026-11-13.lexical_rerank_d10 → rerank-effect-dev-2026-11-13.lexical_rerank_d30 | nDCG@10 | +0.0878 | 0.0000 | 差異顯著 |
| rerank-effect-dev-2026-11-13.lexical_rerank_d20 → rerank-effect-dev-2026-11-13.lexical_rerank_d30 | R@1 | +0.0269 | 0.0013 | 差異顯著 |
| rerank-effect-dev-2026-11-13.lexical_rerank_d20 → rerank-effect-dev-2026-11-13.lexical_rerank_d30 | R@5 | +0.0379 | 0.0001 | 差異顯著 |
| rerank-effect-dev-2026-11-13.lexical_rerank_d20 → rerank-effect-dev-2026-11-13.lexical_rerank_d30 | R@10 | +0.0379 | 0.0002 | 差異顯著 |
| rerank-effect-dev-2026-11-13.lexical_rerank_d20 → rerank-effect-dev-2026-11-13.lexical_rerank_d30 | R@20 | +0.0442 | 0.0000 | 差異顯著 |
| rerank-effect-dev-2026-11-13.lexical_rerank_d20 → rerank-effect-dev-2026-11-13.lexical_rerank_d30 | R@50 | +0.0000 | 1.0000 | 未通過檢定 |
| rerank-effect-dev-2026-11-13.lexical_rerank_d20 → rerank-effect-dev-2026-11-13.lexical_rerank_d30 | MRR | +0.0294 | 0.0000 | 差異顯著 |
| rerank-effect-dev-2026-11-13.lexical_rerank_d20 → rerank-effect-dev-2026-11-13.lexical_rerank_d30 | nDCG@10 | +0.0339 | 0.0000 | 差異顯著 |
