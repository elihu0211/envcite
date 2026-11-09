# 融合參數消融與可答性下界落差：在真實語料上的執行（2026-11-09）

兩個評估工具（`tools/ablation_fusion_eval.py`、`tools/threshold_calibration.py`）先前沒有任何結果檔，對現有成品實跑得 0 題與 0 落差。本報告先查根因，修好工具，再在全庫嵌入的真實語料上實跑，並照專論 04-04 的方法（qrels／run 分離、ranx、Fisher 隨機化檢定、開發集選、測試集只報一次、Hole@10、雙模型判定）報告。

## 結論

| # | 結論 | 證據 | 不能說的 |
|---|---|---|---|
| 1 | 先前的 0 題與 0 落差是**工具與輸入不符**，不是資料沒有落差：讀錯鍵、缺的欄位被預設成「與另一深度相同」、語意臂的 run 沒有任何工具產生 | 下節「根因」；修正有測試，且用真實資料庫跑通 | — |
| 2 | 每臂候選池 P：P = 30 比現行的 50 差（開發集 nDCG@10 −6.7%，過檢定）；P = 100 比 50 好（開發集 +7.8%、測試集 +2.9%，兩邊都過檢定；MRR 兩邊都無差） | 表 1、表 3 | 沒有在別的語料或別的分詞器上驗證；P = 100 的增益推測來自字面臂變深（部署設定下語意臂沒有更多候選，見 5），字面臂深度的單獨效果沒有量 |
| 3 | 名次倒數常數 k：P = 50 下 k = 20／60／100 的 nDCG@10 差距不超過 0.003；k = 20 的 MRR 在開發集高 3.8%（校正後 p = 0.048），沒有在測試集驗證 | 表 1 | k 沒有測試集的證據；k 只在開發集的單一語料上看過 |
| 4 | **語意臂單獨比任何一種融合都好**，字面臂單獨遠比兩者差：nDCG@10 開發集 0.457（semantic-only）> 0.430（P100-k60）> 0.399（現行 P50-k60）≫ 0.171（lexical-only）；測試集 0.476 > 0.443 > 0.430，semantic-only 與 P50-k60 的差、與 P100-k60 的差都過檢定，MRR 的差沒有過 | 表 1、表 3 | 只是檢索層的排序品質；不含作答，也不代表可以拿掉字面臂（字面臂對沒被問句點名的法規、或題庫以外的查詢型態是否有用，這裡沒有量） |
| 5 | 部署設定下語意臂的候選比候選池少得多：pgvector 的 HNSW 一次最多回 `hnsw.ef_search`（預設 40）列，`LIMIT` 設到 400 也只回 40 列。語意臂每題候選條數的中位數是 31（開發集）、35（測試集），P = 50 與 100 都一樣；達到候選池的只有問句點名法規的題（改走範圍內精確掃描；開發集 76 題點名法規，其中 70 題達到，測試集 87 題中 81 題），沒點名法規的題一律不到 50。把 `ef_search` 調到 400 後，各格 nDCG@10 高 2.3%–5.5%，但 Holm 校正後只有 P100-k100 過檢定 | 表 2；`near_probe` 欄位 | 沒有量 staging 的設定與計畫，本機是 pgvector 0.8.7、預設設定、計畫為 HNSW 索引掃描；未過檢定不等於沒有差（170 題） |
| 6 | 罰則帶出（`search_expanded`）把服務對外的清單改成與純融合不同：開發集 72／170 題、測試集 60／171 題的次序被改動；R@10 高 0.100（開發集 p = 0.0013）、0.076（測試集 p = 0.0117），MRR 高 0.043／0.033，nDCG@10 不顯著 | 表 4 | 這項不在事先登記的分析裡（探索性）；消融本身量的是帶出之前的融合清單 |
| 7 | 可答性下界的**截斷落差**在部署的下界（字面 0.5、語意 0.71）下很小：可答題前 50 條過下界而前 10 條不過的，開發集 1／170（0.6%，95% CI 0.1%–3.3%）、測試集 1／171；無答案題 0／47、0／47 | 表 5 | 無答案題只有 47 題；下界換成別的值時的落差沒有在測試集重量 |
| 8 | 這組下界**幾乎不擋無答案問句**：無答案題通過下界的比例開發集 74%（35／47）、測試集 87%（41／47），可答題通過 96.5%／92.4% | 表 5 | 不是「下界該改成多少」的建議；那是產品取捨 |
| 9 | `answerability-floor-2026-11-02.md` 寫「`consult` 採用字面 4、語意 0.61」，程式（a8b11c5 起）是 0.5／0.71；該文件的描述已與程式不符 | `consult` 的 appsettings.json | 該文件的數字當時的量測仍成立，只是不是現行值 |

## 先前量到 0 的根因

| 現象 | 根因 | 修正 |
|---|---|---|
| `threshold_calibration.py` 對 `golden-retrieval-dev-2026-11-04.json` 得 `total_questions` = 0 | 工具讀頂層 `rows`／`results`，`measure` 的輸出是 `arms.<臂>.results` | 讀 `arms.<臂>.results`；不是這個形狀的報告整份拒絕 |
| 對 `grounding-check-2026-11-03.json` 得落差 0 | 該檔沒有任何分數欄；工具把缺的 `top50_*` 預設成與 `top_*` 相同，**落差 0 是建構出來的**。連 `measure` 也只存前 k = 50 條的最高分，沒有前 10 條的，截斷無從隔離 | `measure` 另記前 10 條（`CONSULT_LIMIT`，consult 讀的）的最高分；缺欄位、前 10 條高於前 k 條、讀不到 consult 的下界，一律停，不補預設；下界取自 consult 的 appsettings.json |
| `ablation_fusion_eval.py` 範例引用的語意臂 run 不存在 | 沒有任何工具產生語意臂單獨的 run（`measure` 只有字面與混合） | `arms` 經服務自己的 `retrieval.search_hybrid` 取字面臂（不帶嵌入）、語意臂（空詞串：空 tsquery 不比對任何字面）與融合清單，唯讀 |
| 就算有 run 檔也是 0 題 | run 檔的鍵是 `ranking`，工具讀 `run`，`.get("run", 整份文件)` 退回整份文件，每題的名單都是空的 | 不再有退路，格式不合就停 |
| 離線融合與服務不同 | 同分次序用條文代號字典序（服務用條文現行版本編號）；名次倒數用浮點 | 同分依版本編號、分數用 `Fraction`；`grid` 先逐題核對離線融合與服務的 `search_hybrid`，不一致就停（實跑：每個候選池、每一輪 170／170 與 171／171 題完全相同） |
| 判定工具（`calibrate`、`pool`）在真實資料庫上根本跑不起來 | `golden_set.article_texts` 以 `r.jurisdiction = w.jur` 比較列舉型別與文字，PostgreSQL 直接報錯；單元測試沒走過這條查詢 | 轉型比較；新測試以真實資料庫為準，且確認未修時會失敗 |

## 條件定義

| 項目 | 內容 |
|---|---|
| 語料 | 全國法規資料庫 `moj_law`、`moj_order`，資料日 2026-10-30；11,804 部法規、911,544 個條文單元、650,230 筆 passage，**全數**以 `bge-m3-embedding`（baai）嵌入；分詞器 `lexbigram-1+dict-94450239`；由 `envcite-migrate` 與 `envcite-sync moj` 建在拋棄式本機 PostgreSQL 18（pgvector 0.8.7，`hnsw.ef_search` = 40，`iterative_scan` = off）。同步先跑字面臂，再帶 `ENVCITE_ONTOLOGY_MODELS` 補嵌入；嵌入耗時約 7.5 小時（含一次 44 分鐘的端點停滯，自動恢復） |
| 題庫 | `regulations-golden-eval.yaml`，sha256 `6609967f…`，471 題。這份題庫已不在 chepb 倉庫的工作樹，從其 git 歷史（commit `257ea8002e`）還原；切分重現得到與已提交的 `golden-split.json` 相同的 sha256 `2e4dfd13…`（種子 `envcite-golden-1`；開發集 234 題、測試集 237 題） |
| qrels | 對本語料重建（341 題可答、36 題 unmapped、94 題無答案），期望條文對映與舊版逐題相同；題庫給定的條分數 2，其餘由模型判定；相關門檻 2；`eval/golden-qrels-2026-11-09.json` |
| 計分的題 | 開發集 170 題、測試集 171 題（可答且對得到語料）；unmapped 的 17／19 題沒有跑檢索；無答案題 47／47 題只用於下界 |
| 受測系統 | 檢索：服務的 `retrieval.search_hybrid`（問句點名法規縮小範圍與其授權命令、無點名時全庫；`max_df` = 0）。作答模型在受測路徑上但這裡不量作答。系統家族：嵌入 baai、作答 mistral |
| 判定者 | `nvidia:nemotron-3.5-super-vl`（nvidia）與 `qwen:ornith-35b`（qwen），都不屬受測系統家族，判定前以 `SameModel` 探測別名；零樣本 UMBRELA 提示（sha256 `c0a56bc0…`，與 2026-11-04 校準時相同）、溫度 0；每條各判一次，最終分數取兩人較低者，任一人判不出來就仍是洞。**沒有人工判定** |
| 判定一致度 | 開發集池 κ（二元，≥ 2 為相關）0.699、二次加權 κ 0.657；測試集池 0.737、0.659。校準（開發集已知正例與同法規隨機負例）：保守分數召回 72.6%、誤判 2.5%（nemotron 74.8%／3.9%，ornith 82.4%／5.4%），κ 0.829／0.799。2026-11-04 的校準為召回 75.2%、誤判 5.3%、κ 0.872 |
| 判定池 | 開發集：各格（含字面、語意對照與兩種 `ef_search`）及 `measure` 的前 10 條共 4,725 對，6 對判不出來；測試集：P50-k60、P100-k60、semantic-only 與 `measure` 混合臂共 3,226 對，14 對判不出來（都是 ornith 重試後仍失敗）。開發集 Hole@10 為 0.06%–0.29%，測試集三格 0.29%–0.41% |
| 格子 | 候選池 P ∈ {30, 50, 100} × 名次常數 k ∈ {20, 60, 100}；現行基準是 P = 50（`SEARCH_POOL`）、k = 60（`search_hybrid` 內寫死）。對照：lexical-only、semantic-only（P = 50） |
| 輪數 | 服務設定的條件：開發集與測試集各 3 輪；`ef_search` = 400 的條件：開發集 3 輪。**服務設定下 3 輪的清單逐位相同**（檢索是確定性的），所以輪間範圍是 0，「三輪」沒有提供獨立重複，判有差靠的是逐題的成對檢定；`ef_search` = 400 時只有 semantic-only 對照有 0.0003 的輪間差（nDCG@10） |
| 判有差 | 至少 3 輪、各輪方向一致、每輪 Holm 校正後 p < 0.05、平均差大於兩格的輪間範圍。檢定：ranx 成對 Fisher 隨機化（10,000 次置換）。Holm 在同一個比較族（對基準、對 semantic-only、`ef_search` 兩條件之間）、同一輪、同一指標內校正 |
| 事先登記 | 開發集定案後、測試集任何指標算出之前登記：測試集只報 P50-k60、P100-k60、semantic-only，驗證性比較為 ① P100-k60 − P50-k60、② semantic-only − P50-k60、③ semantic-only − P100-k60（nDCG@10、MRR）。實作時 Holm 是在「對基準」與「對 semantic-only」兩個族內各自校正；改以登記的三個比較合起來校正（由各輪未校正 p 值重算），nDCG@10 的校正後 p 為 ② ≈ 0.002、①與③ ≈ 0.04（0.038），MRR 仍無一過檢定，結論不變 |
| 環境 | 拋棄式容器 `envcite-eval-pg`（127.0.0.1:55432），沒有碰 staging 或任何共用資料庫；資料只經 `envcite-migrate`、`envcite-sync moj` 寫入；模型金鑰由服務自己的設定從作業系統鑰匙圈讀取，沒有經過命令列、日誌或本報告 |

## 融合消融

### 表 1　開發集（170 題，服務設定 `ef_search` = 40，3 輪相同）

| 格 | R@1 | R@5 | R@10 | MRR | nDCG@10 | Hole@10 | nDCG@10 對基準 | MRR 對基準 |
|---|---|---|---|---|---|---|---|---|
| P30-k20 | 0.159 | 0.329 | 0.423 | 0.243 | 0.369 | 0.24% | −0.030（−7.4%）＊ | +0.001（+0.4%） |
| P30-k60 | 0.159 | 0.318 | 0.418 | 0.241 | 0.372 | 0.24% | −0.026（−6.7%）＊ | −0.001（−0.2%） |
| P30-k100 | 0.159 | 0.318 | 0.418 | 0.241 | 0.372 | 0.24% | −0.027（−6.7%）＊ | −0.001（−0.2%） |
| P50-k20 | 0.165 | 0.371 | 0.453 | 0.251 | 0.398 | 0.24% | −0.001（−0.3%） | +0.009（+3.8%）＊ |
| **P50-k60（現行）** | 0.159 | 0.347 | 0.447 | 0.242 | 0.399 | 0.29% | 基準 | 基準 |
| P50-k100 | 0.159 | 0.341 | 0.441 | 0.241 | 0.396 | 0.29% | −0.003（−0.6%）＊ | −0.001（−0.3%）＊ |
| P100-k20 | 0.171 | 0.382 | 0.459 | 0.263 | 0.414 | 0.24% | +0.015（+3.7%）＊ | +0.021（+8.5%） |
| P100-k60 | 0.165 | 0.365 | 0.471 | 0.253 | 0.430 | 0.29% | +0.031（+7.8%）＊ | +0.011（+4.7%） |
| P100-k100 | 0.165 | 0.353 | 0.459 | 0.250 | 0.425 | 0.29% | +0.026（+6.6%）＊ | +0.008（+3.5%） |
| lexical-only | 0.041 | 0.094 | 0.141 | 0.079 | 0.171 | 0.06% | −0.228（−57.1%）＊ | −0.163（−67.4%）＊ |
| semantic-only | 0.247 | 0.412 | 0.488 | 0.317 | 0.457 | 0.12% | +0.058（+14.5%）＊ | +0.075（+30.9%）＊ |

＊ 依「判有差」的規則成立。注意兩點：P50-k100 的 −0.003 雖然過檢定，實質可忽略；服務設定下輪間範圍是 0，所以任何方向一致且過檢定的差都算有差，要看差的大小而不只看＊。

各桶 nDCG@10（開發集，描述用，沒有逐桶檢定）：

| 桶 | 題數 | P50-k60 | P100-k60 | semantic-only | lexical-only |
|---|---|---|---|---|---|
| exact_id | 42 | 0.474 | 0.492 | 0.506 | 0.288 |
| paraphrase | 90 | 0.380 | 0.432 | 0.459 | 0.117 |
| multi_hop | 38 | 0.361 | 0.355 | 0.396 | 0.169 |

### 表 2　語意臂的候選與 `ef_search`

| 條件 | 近鄰查詢 `LIMIT` 400 實際回的列數 | 語意臂候選條數（開發集，P = 30／50／100 的中位數）| 達到候選池的題數（P = 30／50／100，共 170 題）|
|---|---|---|---|
| 服務設定（`ef_search` = 40） | 40 | 30／31／31 | 93／70／70 |
| `ef_search` = 400 | 400 | 30／50／100 | 166／164／164 |

`ef_search` = 400 減 40，開發集 nDCG@10（沿用表 1 的格）：P30 三格 +0.012～+0.013（+3.3%～+3.4%）、P50-k20／k60／k100 +0.012／+0.016／+0.016（+3.0%／+4.0%／+4.1%）、P100-k20／k60／k100 +0.011／+0.021／+0.023（+2.6%／+4.9%／+5.5%），semantic-only +0.010（+2.3%）。Holm 校正後只有 P100-k100 過檢定（＊）。這一組條件的檢索在 `ef_search` = 400 時不再逐位確定（只有 semantic-only 對照出現 0.0003 的輪間差）。

### 表 3　測試集（171 題，只報事先登記的三格，3 輪相同）

| 格 | R@1 | R@5 | R@10 | MRR | nDCG@10 | Hole@10 | nDCG@10 對基準 | MRR 對基準 |
|---|---|---|---|---|---|---|---|---|
| P50-k60（現行） | 0.199 | 0.345 | 0.415 | 0.271 | 0.430 | 0.29% | 基準 | 基準 |
| P100-k60 | 0.199 | 0.357 | 0.415 | 0.271 | 0.443 | 0.29% | +0.013（+2.9%）＊ | +0.001（+0.2%） |
| semantic-only | 0.228 | 0.380 | 0.468 | 0.304 | 0.476 | 0.41% | +0.046（+10.8%）＊ | +0.033（+12.3%） |

semantic-only 減 P100-k60：nDCG@10 +0.034（+7.1%，＊）、MRR +0.033（未過檢定）。各桶 nDCG@10（P50-k60／P100-k60／semantic-only）：exact_id 0.460／0.446／0.491、paraphrase 0.444／0.476／0.520、multi_hop 0.365／0.365／0.361。

測試集的 `nDCG@10` 差距比開發集小（P100-k60：+2.9% 對 +7.8%），方向一致；MRR 在兩邊都沒有差。

### 表 4　罰則帶出（探索性，不在事先登記內）

服務對外的清單（`store.search`，含 `search_expanded` 帶出處罰某義務條的罰則條）減去同一候選池的純融合清單（P50-k60）：

| 子集 | R@5 | R@10 | R@50 | MRR | nDCG@10 |
|---|---|---|---|---|---|
| 開發集（170 題） | +0.053（p = 0.14） | +0.100（p = 0.0013） | +0.124（p < 0.0001） | +0.043（p = 0.0002） | +0.017（p = 0.055） |
| 測試集（171 題） | +0.059（p = 0.041） | +0.076（p = 0.012） | +0.082（p < 0.0001） | +0.033（p = 0.0022） | +0.011（p = 0.24） |

（`eval/ablation-expansion-{dev,test}-2026-11-09.{json,md}`；未做多重比較校正。）服務清單與純融合清單的關係：開發集 98 題完全相同（罰則條只是插入）、72 題次序被改動（罰則帶出把融合清單後段的條拉到前面，長度上限再截掉其間的條）、0 題無法由離線融合說明；測試集 111／60／0。

## 可答性下界的截斷落差

下界取自 consult 的 `Relevance` 設定（字面 0.5、語意 0.71），consult 向檢索要 10 條（`OntologySearch.Limit`）。`measure` 的混合臂取前 k = 50 條，另記前 10 條的最高分；一題通過 = 任一條達字面下界或語意下界。前 10 條是前 50 條的前綴，所以只可能「前 50 條過、前 10 條不過」。

### 表 5

| 子集 | 組 | 題數 | 前 50 條過 | 前 10 條過 | 截斷落差 | 落差率（95% CI）|
|---|---|---|---|---|---|---|
| 開發集 | 可答 | 170 | 165（97.1%） | 164（96.5%） | **1**（b-05） | 0.6%（0.1%–3.3%） |
| 開發集 | 無答案 | 47 | 35（74.5%） | 35（74.5%） | 0 | 0%（0%–7.6%） |
| 測試集 | 可答 | 171 | 159（93.0%） | 158（92.4%） | **1**（b-08） | 0.6%（0.1%–3.2%） |
| 測試集 | 無答案 | 47 | 41（87.2%） | 41（87.2%） | 0 | 0%（0%–7.6%） |

兩個落差題都是 multi_hop。下界網格（`golden-truncation-*.json` 的 `grid_top10`）只供描述：開發集上 Youden J（可答通過率減無答案通過率）最大的點是字面 4.0、語意 0.71（可答只剩 50%、無答案放過 21%，J = 0.29），在測試集上 J = 0.23；現行下界 J = 0.22（開發集）、0.05（測試集）。不據此建議改下界，無答案題只有 47 題。

## 限制

- **只有一個本機資料庫，不是 staging。** pgvector 版本、`ef_search`、查詢計畫、資料日與 staging 是否相同沒有量；表 2 的結論只對這個設定成立。
- **判定全是模型**，沒有人工。保守分數（兩人較低者）在已知正例上的召回只有 72.6%，所以絕對的召回與 nDCG 偏低；各系統共用同一個判定池，系統之間的比較受此影響較小，但仍可能偏向判定者較認同的排序方式。判定者 κ 0.70–0.74，不算高。
- **樣本小**：計分題開發集 170、測試集 171，無答案題各 47。未過檢定不等於沒有差。
- **重複輪沒有提供獨立重複**：服務設定下 3 輪逐位相同，只能說檢索是確定性的；檢定是對題目的成對隨機化，不是對輪次。
- **測試集只判了三格**：沒有判完洞的格子（其他 P、k 組合、lexical-only）不報測試集；k 的效果沒有測試集的證據。
- **融合在罰則帶出之前量**。服務對外的清單與純融合不同（表 4），消融的排序不等於使用者看到的排序；兩者之間的交互沒有量。
- **只有一個語料快照與一個題庫**（471 題，題庫由人撰寫、檢索層）；不含作答、延遲，也不含 `semantic-only` 在題庫以外的查詢型態上的表現。
- **快照**：首次同步存了快照 1、2；補嵌入時再次下載，同資料日、同筆數但壓縮檔位元組不同（存為快照 3、4，匯入時全部略過、語料沒變）。qrels 以快照 3、4 重建，對映與以快照 1、2 建的逐題相同。
- **字面臂的數字與 2026-11-04 的報告不同，原因沒有查出**：同一份切分、同為只含題庫給定條的 qrels（判定併入之前的 `measure`，未入庫），字面臂開發集 R@10 exact_id 由 0.452 變 0.262、paraphrase 由 0.022 變 0.133。語料快照（10-22→10-30）、分詞詞典、法規範圍擴及授權命令（0047、0053）都變了。不縮小法規範圍的對照（`--no-law-names`）為 0.095／0.111／0.053。
- **下界**：只評估了部署的下界與一個描述性網格；沒有重新校準，也沒有檢查「前 10 條」之外的截斷（如 consult 對引用條數的再截斷）。
- **沒有跑的**：k 在測試集上的效果；`ef_search` 條件在測試集上的對照；各桶的檢定；字面臂在不同查詢型態（點名法規、條號）上的細分。

## 重現

環境：Docker 與 pgvector 映像、`uv`、macOS 鑰匙圈裡的 `ENVCITE_LLM_API_KEY`（服務自己讀，不經命令列）。以下在 `services/ontology/` 執行，`<題庫>` 是還原的 `regulations-golden-eval.yaml`（sha256 `6609967f…`），`<工作目錄>` 放在倉庫外。

```bash
docker run -d --name envcite-eval-pg -p 127.0.0.1:55432:5432 -v envcite-eval-pgdata:/var/lib/postgresql \
  -e POSTGRES_USER=envcite -e POSTGRES_PASSWORD=envcite -e POSTGRES_DB=envcite pgvector/pgvector:pg18 \
  -c fsync=off -c synchronous_commit=off -c full_page_writes=off -c shared_buffers=2GB
export ENVCITE_ONTOLOGY_DSN=postgresql://envcite:envcite@127.0.0.1:55432/envcite
export ENVCITE_ONTOLOGY_SNAPSHOT_DIR=<工作目錄>/snapshots ENVCITE_ONTOLOGY_MODELS=$PWD/models.toml
uv run envcite-migrate
env -u ENVCITE_ONTOLOGY_MODELS uv run envcite-sync moj     # 字面臂
uv run envcite-sync moj                                     # 補嵌入（約 650,000 段；中斷後重跑續做；失敗的批要再跑一次）

uv run python tools/golden_retrieval_eval.py qrels <題庫> --out <工作目錄>/qrels-base.json
uv run python tools/relevance_judge.py calibrate <題庫> --qrels <工作目錄>/qrels-base.json --split eval/golden-split.json \
  --cache <工作目錄>/judge-cache.jsonl --out eval/golden-calibration-2026-11-09.json
for r in 1 2 3; do uv run python tools/ablation_fusion_eval.py arms <題庫> --qrels <工作目錄>/qrels-base.json \
  --split eval/golden-split.json --subset dev --out <工作目錄>/arms/dev-svc-r$r.json; done        # 測試集同法（--subset test）
for r in 1 2 3; do ... --subset dev --ef-search 400 --out <工作目錄>/arms/dev-ef400-r$r.json; done
uv run python tools/ablation_fusion_eval.py grid --qrels <工作目錄>/qrels-base.json --arms <…r1> --arms <…r2> --arms <…r3> \
  --export-runs <工作目錄>/runs --out <工作目錄>/grid-prejudge.json                              # 匯出各格的 run，供判定
uv run python tools/golden_retrieval_eval.py measure <題庫> --qrels <工作目錄>/qrels-base.json --split eval/golden-split.json \
  --subset dev --out <工作目錄>/measure-dev                                                    # 供判定池與下界落差
uv run python tools/relevance_judge.py pool <題庫> --qrels <工作目錄>/qrels-base.json --split eval/golden-split.json \
  --subset dev --run <各格 run> --run <measure 的混合臂 run> --calibration eval/golden-calibration-2026-11-09.json \
  --cache <工作目錄>/judge-cache.jsonl --workers 16 --out eval/golden-judgements-dev-2026-11-09.json   # 失敗的呼叫再跑一次會補
uv run python tools/golden_set.py judge --qrels <工作目錄>/qrels-base.json --judgements eval/golden-judgements-dev-2026-11-09.json --out <工作目錄>/q1.json
uv run python tools/golden_set.py judge --qrels <工作目錄>/q1.json --judgements eval/golden-judgements-test-2026-11-09.json --out eval/golden-qrels-2026-11-09.json
uv run python tools/ablation_fusion_eval.py grid --qrels eval/golden-qrels-2026-11-09.json --arms <dev 三輪> \
  --other-arms <ef400 三輪> --golden <題庫> --out eval/ablation-fusion-dev-2026-11-09.json
uv run python tools/ablation_fusion_eval.py grid --qrels eval/golden-qrels-2026-11-09.json --arms <test 三輪> \
  --only P50-k60 P100-k60 semantic-only --golden <題庫> --out eval/ablation-fusion-test-2026-11-09.json
for s in dev test; do
  uv run python tools/golden_retrieval_eval.py measure <題庫> --qrels eval/golden-qrels-2026-11-09.json \
    --split eval/golden-split.json --subset $s --out eval/golden-retrieval-$s-2026-11-09
  uv run python tools/threshold_calibration.py --eval eval/golden-retrieval-$s-2026-11-09.json \
    --out eval/golden-truncation-$s-2026-11-09.json
done
```

`arms` 的輸出（各輪候選清單，數 MB）不入庫，雜湊記在 `ablation-fusion-*.json` 的 `inputs.arms`；判定快取含題目全文，留在倉庫外。語料來自會更新的外部來源，重建後資料日若不同，數字會不同。

## 檔案

| 檔案 | 內容 |
|---|---|
| `ablation-fusion-{dev,test}-2026-11-09.json` | 各格各輪指標、對基準／semantic-only／另一條件的檢定（含未校正與 Holm 校正的 p）、與服務逐題核對、語意臂深度、近鄰探測 |
| `golden-truncation-{dev,test}-2026-11-09.json` | 截斷落差（含落差題號）與下界網格 |
| `golden-retrieval-{dev,test}-2026-11-09.{json,md}`、`….<臂>.run.json` | `measure` 的生產路徑報告與 run（測試集的字面臂沒有判完洞，Hole@10 46%，只是下界） |
| `ablation-expansion-{dev,test}-2026-11-09.{json,md}` | 罰則帶出對純融合的比較（探索性） |
| `golden-qrels-2026-11-09.json`、`golden-judgements-{dev,test}-2026-11-09.json`、`golden-calibration-2026-11-09.json` | 本語料的 qrels、模型判定與判定者校準 |
