# deploy

Kubernetes 清單。**目前只涵蓋能跑的東西**——`services/consult` 沒有 adapter、匯入器尚未存在，故兩者都不在這裡。缺席是狀態，不是遺漏。

## 硬性前提

| 項目 | 版本 | 為什麼 |
|------|------|--------|
| PostgreSQL | **18+** | `extension_control_path` 是 image volume 擴充能運作的前提 |
| Kubernetes | **1.35+** | ImageVolume 支援 |
| 容器執行環境 | containerd 2.1+ 或 CRI-O 1.31+ | 同上 |

**版本不足時這些清單仍套得上去，但擴充不會掛載**，錯誤會在 `Database` 調和時以 `CREATE EXTENSION` 失敗出現，而不是在套用時。先確認版本，不要靠錯誤訊息回推。

## 內容

| 檔 | 內容 |
|----|------|
| `postgres.yaml` | CloudNativePG `Cluster`（3 副本）與 `Database`；pg_search 與 pgvector 以 image volume 掛載 |
| `migrate-job.yaml` | 綱要遷移，**獨立 Job 而非伺服器啟動時執行** |
| `pooler.yaml` | PgBouncer 連線池（`type: rw`，transaction pooling） |
| `ontology.yaml` | 本體服務 Deployment 與 Service |

## 詞彙檢索為什麼不用擴充

一度用過 ParadeDB（`pg_search`）做 BM25，因為它內建 CJK 分詞。**查證後換掉了**：ParadeDB Community 的官方文件明文，其索引「does not get physically replicated and won't be available on other nodes in a high availability setup」，而 HA 的 ParadeDB 查詢「requires ParadeDB Enterprise」。

後果不是報錯而是安靜的：被提升的 standby 會只用向量臂作答，回傳一份比較短、卻看起來完整的清單。

現在詞彙臂是 **PostgreSQL 內建全文檢索**，分詞在應用層做，索引是標準 GIN——**它會複製、能在 replica 上服務、failover 不必重建**。這也讓連線池回到 `type: ro`。

順帶兩個收穫：擴充清單只剩 `vector` 一項（每一項都是叢集必須掛載映像、且可能擋住 Postgres 主版本升級的東西），以及分詞詞典可以換成台灣的——ParadeDB 的 Lindera 中文分詞用 CC-CEDICT，是大陸詞庫。

代價寫在該寫的地方：**索引時與查詢時的分詞器必須一致**，漂移會讓檢索安靜地變差。處理方式與 `embedding_model` 相同——記在資料列上，查詢時比對，不一致時該列不參與詞彙臂。

## 兩個刻意的決定

**擴充不烘進自建映像。** 核心用 `18-minimal-trixie`，pg_search 取 ParadeDB 的 extension image、pgvector 取 CNPG 社群映像。擴充升級因此不必重建 Postgres。

**遷移不在伺服器啟動時跑。** 那會讓每個副本都成為寫入端，把一次 rollout 變成競爭；做成 Job 才有開始與結束可供等待，失敗也才會呈現為失敗的 Job 而不是不斷重啟的副本。

## 映像參照的守門

`kustomize build` 與 `kubeconform` **都不連 registry**，所以清單指名一個根本不存在的映像時，兩者全綠。這在本倉庫發生過兩次，兩次都在合併之後才查出來：

| 寫成 | 實際 |
|------|------|
| `paradedb-extension:0.20.1-18-trixie` | 該映像只發行過 `0.25.x` |
| `pgvector:0.8.1-18-minimal-trixie` | `-minimal-` 是 Postgres 基底映像的變體，擴充映像沒有 |

`deploy` job 因此多一步：把 `kustomize build` 的輸出交給 `scripts/extract-image-refs.py` 列出所有映像，再逐一 `crane manifest` 查存在性。

**第三方映像查不到即失敗**；**本倉庫自建的映像明示跳過**並印出理由——它由建置 job 產生，而建置 job 尚不存在，在那之前查不到不代表清單寫錯。建置 job 落地時，這個跳過要一併移除。

腳本列舉鍵名（`image`、`imageName`、`reference`）而不猜測。CRD 新增鍵名時要補，而補的動作在 review 看得見；靠形狀猜測則會在沒人察覺時停止找到東西。

## 尚未決定

- **備份目標未選**，故 `Cluster` 沒有 `backup` 區段——指向不存在之處的備份設定，讀起來像備份已經設好了。
- **叢集尚未存在**：本目錄的清單**從未套用過**，僅經 `kubectl --dry-run=client` 與 kustomize build 驗證。
- **分詞器尚未選定**。0010 只規定「分詞在資料庫之外、結果記在資料列上」，實際用哪一個（jieba、台灣特化詞庫、或其他）還沒決定，也還沒有共用的分詞程式庫——匯入器與查詢端必須用同一個，那是下一步。
