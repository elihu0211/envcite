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

## 🚨 ParadeDB Community 不支援 physical replication

官方文件明文：ParadeDB 索引**不會被 physically replicated**，在高可用拓撲的其他節點上不可用；HA 的 ParadeDB 查詢**需要 Enterprise**。

我們用的是 Community（`paradedb/paradedb-extension`）。後果有三，全部都不是理論問題：

**其一，replica 不能服務檢索。** standby 可以跑一般查詢與 pgvector 掃描（HNSW 是標準 Postgres 索引，會複製），但詞彙臂不行。而 `search_articles()` 兩臂都用——**打到 replica 不會報錯，會回傳只有向量臂的結果**，一份比較短、但看起來完整的清單。所以連線池是 `type: rw` 而非 `ro`。

**其二，加 replica 買不到檢索吞吐。** 它買到的是資料的冗餘副本與 failover，不是查詢的並行度。要讀取擴展只能走 Enterprise。

**其三，failover 之後被提升的節點沒有可用的索引。** 必須重建，而**目前沒有任何東西會自動做這件事**。重建在現有語料規模上是秒級（`REINDEX INDEX subdivision_search`），但它需要有人或有東西去觸發。

這是目前最大的未竟事項，而它的解除條件很明確：**Enterprise 授權**，或是把詞彙檢索換成一個會複製的實作。在那之前，`instances: 3` 提供的是資料保護與資料庫層的 failover，**不是檢索層的高可用**。

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
- **failover 後的索引重建未自動化**（見上方 ParadeDB 限制）。手動補救是 `REINDEX INDEX subdivision_search`；自動化要嘛等 Enterprise，要嘛做成一個檢查索引健康的冪等 Job——後者要先確認 ParadeDB 的 `Verify Index Integrity` 能否用來判斷「這個索引在這個節點上是可用的」。
