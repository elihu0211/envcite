# 黃金題庫檢索比較（envcite）

- qrels sha256 `8a7857697f13c576765bfbfc99b349bc8ffa272a3c8191aa07cd7c854e6de852`；切分 sha256 `2e4dfd1335c7002d84f7ac51bce73685e13b61470705a12f4cafa417afbf055e`，子集 **dev**，170 題
- 檢定：Fisher 隨機化檢定（ranx compare，成對、雙尾，10000 次置換，種子 0）；α = 0.05。未通過檢定的差距不寫成改善或退步；Hole@10 偏高時，未判定的條可能改變結論。

| run | R@1 | R@5 | R@10 | R@20 | R@50 | MRR | nDCG@10 | Hole@10 |
|---|---|---|---|---|---|---|---|---|
| base-dev.hybrid | 0.094 | 0.300 | 0.341 | 0.371 | 0.476 | 0.174 | 0.200 | 96.2% |
| cov-dev.hybrid | 0.112 | 0.300 | 0.341 | 0.371 | 0.482 | 0.184 | 0.209 | 96.2% |

| a → b | 指標 | 差（b − a） | p | 結論 |
|---|---|---|---|---|
| base-dev.hybrid → cov-dev.hybrid | R@1 | +0.0176 | 0.3797 | 未通過檢定 |
| base-dev.hybrid → cov-dev.hybrid | R@5 | +0.0000 | 1.0000 | 未通過檢定 |
| base-dev.hybrid → cov-dev.hybrid | R@10 | +0.0000 | 1.0000 | 未通過檢定 |
| base-dev.hybrid → cov-dev.hybrid | R@20 | +0.0000 | 1.0000 | 未通過檢定 |
| base-dev.hybrid → cov-dev.hybrid | R@50 | +0.0059 | 1.0000 | 未通過檢定 |
| base-dev.hybrid → cov-dev.hybrid | MRR | +0.0101 | 0.2830 | 未通過檢定 |
| base-dev.hybrid → cov-dev.hybrid | nDCG@10 | +0.0097 | 0.1549 | 未通過檢定 |
