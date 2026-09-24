# deploy

env-law 的 Kubernetes 清單，**Argo CD 的唯一來源**（`deploy/base/`）。與程式碼同倉：需要程式碼與清單一起改的變更（例如新增必填環境變數）在同一個 MR 生效，自動上線時不會出現新映像配舊清單的錯位；只改清單的 MR 由 `rules:changes` 只跑 `deploy` 驗證。分庫的理由（權限分離、只給 Argo CD 看清單）要等有團隊或正式區審核時才成立，屆時再拆。

**目前只涵蓋能跑的東西**——`services/consult` 沒有 adapter、匯入器尚未存在，故兩者都不在這裡。缺席是狀態，不是遺漏。

## 硬性前提

| 項目 | 版本 | 為什麼 |
|------|------|--------|
| **CloudNativePG** | **1.27+** | `.spec.postgresql.extensions` 自該版引入 |
| PostgreSQL | **18+** | `extension_control_path` 是 image volume 擴充能運作的前提 |
| Kubernetes | **1.35+**，或 1.33+ 開啟 feature gate | ImageVolume 支援 |
| 容器執行環境 | containerd 2.1+ 或 CRI-O 1.31+ | 同上 |

CNPG 1.29 起，正式區的推薦形式是 `ImageCatalog`——由 operator 依 PostgreSQL 主版本解析映像參照與擴充路徑，而不是寫在清單裡。**那正好消除本檔已經犯過一次的兩類錯誤**（不存在的 tag、手寫的路徑）。目前不採用，理由是 catalog 要另外建立與維護，而在只有一個叢集時等於用兩個字面值換一個要維護正確的物件。有第二個叢集、或映像守門不再夠用時再回頭。

**版本不足時這些清單仍套得上去，但擴充不會掛載**，錯誤會在 `Database` 調和時以 `CREATE EXTENSION` 失敗出現，而不是在套用時。先確認版本，不要靠錯誤訊息回推。

## 上線流程

**全自動，不需要開 MR。**

1. env-law 合併到 `main` → `ontology-image` job 推送 `registry.gitlab.com/thuaisse-lab/env-law/ontology:<commit-sha>`
2. Argo CD Image Updater（`argocd/image-updater.yaml`）偵測到新映像，把 Application 改到那顆映像
3. Argo CD（`argocd/env-law.yaml`，automated + prune + selfHeal）同步：資料庫（wave 0）→ 遷移 Job（Sync hook，wave 1）→ 服務（wave 2）

線上跑的 tag 記在 Application 的 kustomize 覆寫上（`argocd` write-back，不寫回 git），它優先於 `base/kustomization.yaml`——後者只是首次部署的基準。回退：先從 `argocd/image-updater.yaml` 拿掉該映像（否則會再被更新回最新），再 `argocd app set env-law --kustomize-image env-law-ontology=registry.gitlab.com/thuaisse-lab/env-law/ontology:<舊 sha>`。

清單本身（副本數、資源、探針等）的變更走一般 MR，由 CI `deploy` job 把關。

## 叢集端前提

清單以外、必須先存在於叢集的東西（機密不進 git）：

| 項目 | 說明 |
|------|------|
| Argo CD、CloudNativePG operator | 叢集附加元件，不在本倉庫 |
| Argo CD repository 憑證 | env-law 為私有；用 GitLab deploy token（`read_repository`） |
| `Secret/env-law-registry` | `kubernetes.io/dockerconfigjson`，GitLab deploy token（`read_registry`）——env-law 的 registry 是私有的 |
| `Secret/env-law-internal-token` | 鍵 `token`；platform 呼叫 ontology 用的內部 token |
| Argo CD Image Updater | 叢集附加元件，不在本倉庫 |
| `argocd` namespace 的 `Secret/env-law-registry` | 與上面同內容；Image Updater 查 registry 用 |
| `argocd/env-law.yaml`、`argocd/image-updater.yaml` | 一次性 `kubectl apply -n argocd`，之後一切由 git 與 Image Updater 驅動 |

## 內容

`base/` 每個部署單元一個目錄、各有自己的 `kustomization.yaml`；映像名稱只在 `base/kustomization.yaml` 解析一次。新增服務就是新增一個目錄並列入 `resources`；有叢集之後，環境差異放 `overlays/<env>/`（在那之前不建）。

| 檔 | 內容 |
|----|------|
| `base/database/cluster.yaml` | CloudNativePG `Cluster`（3 副本）與 `Database`；pgvector 以 image volume 掛載 |
| `base/database/pooler.yaml` | PgBouncer 連線池（`type: ro`，transaction pooling） |
| `base/ontology/migrate-job.yaml` | 綱要遷移，**獨立 Job 而非伺服器啟動時執行** |
| `base/ontology/deployment.yaml` | 本體服務 Deployment 與 Service |
| `argocd/env-law.yaml` | Argo CD `Application`（指向 `deploy/base/`） |
| `argocd/image-updater.yaml` | Image Updater：新映像自動上線 |

## 詞彙檢索為什麼不用擴充

一度用過 ParadeDB（`pg_search`）做 BM25，因為它內建 CJK 分詞。**查證後換掉了**：ParadeDB Community 的官方文件明文，其索引「does not get physically replicated and won't be available on other nodes in a high availability setup」，而 HA 的 ParadeDB 查詢「requires ParadeDB Enterprise」。

後果不是報錯而是安靜的：被提升的 standby 會只用向量臂作答，回傳一份比較短、卻看起來完整的清單。

現在詞彙臂是 **PostgreSQL 內建全文檢索**，分詞在應用層做，索引是標準 GIN——**它會複製、能在 replica 上服務、failover 不必重建**。這也讓連線池回到 `type: ro`。

順帶兩個收穫：擴充清單只剩 `vector` 一項（每一項都是叢集必須掛載映像、且可能擋住 Postgres 主版本升級的東西），以及分詞詞典可以換成台灣的——ParadeDB 的 Lindera 中文分詞用 CC-CEDICT，是大陸詞庫。

代價寫在該寫的地方：**索引時與查詢時的分詞器必須一致**，漂移會讓檢索安靜地變差。處理方式與 `embedding_model` 相同——記在資料列上，查詢時比對，不一致時該列不參與詞彙臂。

## 兩個刻意的決定

**擴充不烘進自建映像。** 核心用 `18-minimal-trixie`，pgvector 取 CNPG 社群映像。擴充升級因此不必重建 Postgres。

**遷移不在伺服器啟動時跑。** 那會讓每個副本都成為寫入端，把一次 rollout 變成競爭；做成 Job 才有開始與結束可供等待，失敗也才會呈現為失敗的 Job 而不是不斷重啟的副本。

## 映像參照的守門

`kustomize build` 與 `kubeconform` **都不連 registry**，所以清單指名一個根本不存在的映像時，兩者全綠。這發生過兩次，兩次都在合併之後才查出來：

| 寫成 | 實際 |
|------|------|
| `paradedb-extension:0.20.1-18-trixie` | 該映像只發行過 `0.25.x` |
| `pgvector:0.8.1-18-minimal-trixie` | `-minimal-` 是 Postgres 基底映像的變體，擴充映像沒有 |

`deploy` job 因此多一步：把 `kustomize build` 的輸出交給 `scripts/extract-image-refs.py` 列出所有映像，再逐一 `crane manifest` 查存在性，**任一查不到即失敗**——env-law 自建的映像也一樣：它的 tag 是建置 job 先推送、才可能被寫進這裡的 commit SHA，查不到就是清單寫錯。私有 registry 以 CI job token 讀取（同一專案，不需 allowlist）。

腳本列舉鍵名（`image`、`imageName`、`reference`）而不猜測。CRD 新增鍵名時要補，而補的動作在 review 看得見；靠形狀猜測則會在沒人察覺時停止找到東西。

## 尚未決定

- **備份目標未選**，故 `Cluster` 沒有 `backup` 區段——指向不存在之處的備份設定，讀起來像備份已經設好了。
- **叢集尚未存在**：這些清單**從未套用過**，僅經 `kubectl --dry-run=client` 與 kustomize build 驗證。
