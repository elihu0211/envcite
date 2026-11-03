# 黃金題庫：qrels、切分與 ranx 指標的第一次量測（2026-11-03）

產物：`golden-qrels.json`（341 題可答、36 題 unmapped、94 題無答案不進 qrels；尚無人工判定）、
`golden-split.json`（種子 `envcite-golden-1`、測試比例 0.5、依桶分層：dev 234 題、test 237 題）、
`golden-retrieval-dev-2026-11-03.{json,md}` 與各臂 run、`golden-compare-dev-2026-11-03.{json,md}`。
測試集也量了一次，只供下面的一致性核對，報告不提交、數字不列：測試集留到定案後報告。

## 量測環境

- 資料庫：本機拋棄式 `envcite-fullembed-pg`（全庫已嵌入），語料快照同 `golden-retrieval-fullcorpus-2026-11-03`。
- 該庫已套用另一分支的遷移 `0046_hybrid_law_scope`（`search_hybrid` 多一個法規範圍參數），main 的
  `store.py` 叫不到新簽章。量測時以該分支的 `src` 為 `envcite_ontology`，`search` 不傳法規範圍，
  遷移註明此時與 0045 逐位元相同；報告的 commit 是本分支的工具。

## 與被取代的自有指標的一致性

- 對 dev、test 兩次量測的每一臂、每一桶與總計，以被刪掉的 `retrieval_eval.recall`／`mrr`
  （任一期望條文在前 k；第一個命中名次的倒數）重算逐題名次，與 ranx 的 `hit_rate@k`、`mrr`
  到小數四位完全相同。
- 字面臂 341 題的逐題名次與 `golden-retrieval-fullcorpus-2026-11-03` 完全相同，dev＋test 合併後的
  R@k 與 MRR 也相同（全部題目的數字舊報告已有）。
- 混合臂有 48 題的名次與該報告不同，合併 R@10 0.226（舊 0.2375）。指標相同、字面臂相同，差異在
  語意臂的取回；原因未查（兩次量測之間有 0045、0046 的檢索改動，也可能是語意臂本身的不確定性）。

## Hole@10

字面臂 dev 99.6%、混合臂 dev 97.2%：每題只有一到數條期望條文，前 10 條大多是未判定的洞。
dev 混合臂匯出待判 1,653 個（170 題）；判定尚未進行，現在的召回是下界。
