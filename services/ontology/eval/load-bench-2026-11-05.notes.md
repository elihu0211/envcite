# 效能基準：consult Ground、ontology 檢索與推論 rpc 的第一次量測（2026-11-05）

數字見同名的 `.md`（表）與 `.json`（逐階原始摘要）。**只在本機量測，不代表部署環境**；沒有在 staging 做負載測試。

## 重點

| 對象 | 循序熱快取 p50／p95／p99 | 飽和吞吐 | 飽和點 |
|------|--------------------------|----------|--------|
| search（混合檢索） | 0.77／1.21／1.52 秒 | 約 11.6 次/秒 | 並行 16 |
| ground（consult 端到端） | 0.76／1.01／1.07 秒 | 約 12.2 次/秒 | 並行 16 |
| evaluate（推論 rpc） | 15／16／16 毫秒 | 約 119 次/秒 | 並行 4 |
| search-lexical（只有字面臂） | 76／316／383 毫秒 | 約 49 次/秒 | 並行 8 |

- **並行 24 起，四個對象都有約 87–92% 的請求回 `RESOURCE_EXHAUSTED`**，是伺服器的同時 RPC 上限（20）在擋，不是失敗：被拒的請求立刻返回，所以該階的 `rps` 與延遲分位數不可當吞吐讀。飽和吞吐取最後一個錯誤率為 0 的階。
- 並行 1 到 8 之間，search 與 ground 的吞吐幾乎線性成長（1.2 → 9.3、1.3 → 9.9 次/秒），延遲不變；到 16 延遲才升（p50 約 1.3 秒），吞吐只多一點。
- evaluate 在並行 2 就到平台（約 115 次/秒），之後延遲隨並行線性加大——單一行程的 CPU 已滿，不是排隊在資料庫。
- ground 比 search 多的時間很小（循序 p50 約持平）：consult 這一層的額外成本在量測誤差內，端到端時間由 ontology 的檢索決定。

## 瓶頸證據（只記錄，不在本 MR 修）

1. **search／ground 的時間幾乎全在嵌入端點。** 同一批問句，只有字面臂的 p50 是 76 毫秒、有嵌入的是 770 毫秒；語意臂的 HNSW 查詢在熱快取下實測約 1 毫秒（`EXPLAIN (ANALYZE, BUFFERS)`：`Index Scan using passage_embedding`，shared hit 1256，執行 0.7–1.1 毫秒），冷時 210 毫秒（讀 897 個頁）。差額約 0.7 秒是每次查詢一次的嵌入 HTTP 呼叫，沒有快取。飽和吞吐約 12 次/秒因此受限於嵌入端點，而不是 ontology 行程或資料庫。
2. **語意臂的候選池被 `hnsw.ef_search` 截在 40。** `search_hybrid` 要求 `LIMIT p_pool * 4`（200），同一個 EXPLAIN 只回 `rows=40`；`hnsw.ef_search` 是預設的 40，沒有在連線上設定。這影響的是召回而非延遲，歸檢索那條線；列在這裡是因為量測時發現。
3. **最慢的單題是 neg-008（5.3 秒，只出現一次）**，其餘最慢的在 1.4–2.4 秒，是嵌入端點的尾延遲，不是查詢計畫。
4. **字面臂**：對三個常見詞的 `tsv @@` 查詢（簡化重現，不含 `force_period`／`expression` 條件），`passage_tsv` 的 bitmap 掃描 3.5 毫秒，但整體 202 毫秒，其中對 `provision_version` 的全表序列掃描（911,497 列）就占 86 毫秒，而 `provision_version_current_by_content` 部分索引存在；這是簡化查詢的計畫，是否在 `search_hybrid` 內同樣發生，本次沒有確認，列為後續。

## 量測定義與限制

- **冷快取**：重啟資料庫容器（清 PG 的 shared_buffers）並重起 ontology 與 consult 行程後，循序一輪。Docker VM 的頁快取與主機的檔案快取**沒有清**，所以冷與熱的差距低估了真正的冷啟動。熱快取是丟棄一輪暖機之後的循序一輪。
- 每階 234 題（黃金題庫開發集，每題送出一次）；並行階梯為 1、2、4、8、16、24、32。k6 的 shared-iterations，每個 VU 一條連線。
- 沒有「已確認的陳述」（0 筆），所以 evaluate 只量到規則評估與請求路徑，不含真實事實量下的成本；它的數字是下界。
- 嵌入端點是遠端服務，其延遲隨時段與負載變動；search 與 ground 的數字包含它。
- 嵌入端點呼叫次數：建庫的 `envcite-sync moj` 嵌入 650,197 段，批次 32，約 2 萬次（中途因 HNSW 索引拖慢插入而停止，丟掉索引後續跑，最後重建）；基準本身 4,680 次（20 個有嵌入的階段 × 234 題）。
- 索引參數：`passage_embedding` 為 `hnsw (embedding vector_cosine_ops)`，`m` 與 `ef_construction` 用預設（16、64），5080 MB；以 `maintenance_work_mem='2GB'`、`max_parallel_maintenance_workers=0` 建（平行建構在容器內因共享記憶體不足失敗）。`hnsw.ef_search` 為預設 40。

## 重現

```bash
docker run -d --name envcite-bench-pg -p 127.0.0.1:55950:5432 -e POSTGRES_USER=envcite -e POSTGRES_PASSWORD=<密碼> -e POSTGRES_DB=envcite pgvector/pgvector:pg18 -c shared_buffers=2GB
uv run --env-file <env> envcite-migrate
uv run --env-file <env> envcite-sync moj
uv run --env-file <env> python tools/load_bench.py run <golden.yaml> --split eval/golden-split.json --pg-container envcite-bench-pg --out eval/load-bench-<日期>
```

`<env>` 要有資料庫 DSN、快照目錄與 `ENVCITE_ONTOLOGY_MODELS`；模型金鑰從 macOS 鑰匙圈取，不寫進檔案。建庫時嵌入量大，索引維護會拖慢插入，可在 `envcite-sync` 前先 `DROP INDEX retrieval.passage_embedding`，投影完後再建。需要 k6（`brew install k6`）。
