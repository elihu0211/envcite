# 參與開發

本檔說明變更怎麼送進 `main`，以及本機開發環境怎麼建立。各服務的建置、測試指令見 [`AGENTS.md`](AGENTS.md) 與各服務的巢狀 `AGENTS.md`；設計論據在同層的 `env-law-docs` 倉庫。

## 貢獻流程

1. **從 `main` 開分支**，命名 `<type>/<簡述>`（如 `feat/…`、`fix/…`、`docs/…`）。平行作業每條分支一棵 worktree，見 `AGENTS.md`「平行 worktree」。
2. **Commit 採 [Conventional Commits](https://www.conventionalcommits.org)**：`type(scope): 說明`，破壞性變更加 `!`（如 `feat(consult)!: …`）。
3. **開 MR 到 `main`**。`main` 受保護，不直接推送；pipeline 成功才能合併。MR 以 squash 合併，合併後刪除來源分支。
4. **合併前的要求**見 `AGENTS.md`：「Done when」（受影響服務的 lint、typecheck、test 與 `buf lint`）、「Contract」（改契約時兩側產生碼同一個 MR 更新），以及與 `env-law-deploy` 一起改時清單先行。
5. **改動設計時**，決策的論據寫進 `env-law-docs`，不寫進本倉庫。

## 環境概覽

兩套環境，用途不同，都跑在 [OrbStack](https://orbstack.dev) 的 Docker 上：

| 環境 | 用途 | 資料庫 |
|------|------|--------|
| **主機直跑**（`wt platform`／`wt ontology`／`wt consult`） | 日常開發、除錯 | 容器 `env-law-dev-pg`，`localhost:5432` |
| **kind 叢集** `env-law`（context `kind-env-law`） | 驗證 `env-law-deploy` 的清單 | 叢集內 CloudNativePG `env-law-pg` |

**OrbStack 內建的 Kubernetes 保持關閉**（`orbctl config set k8s.enable false`）。它以 Docker 引擎為容器執行環境，不支援 ImageVolume，pgvector 掛不上；kind 的節點是 containerd 2.x，清單原樣可用。細節見 `env-law-deploy` 的 README「本機叢集」。

## 主機直跑的 PostgreSQL

與 ontology 測試同一個映像（`services/ontology/tests/conftest.py`），內含 `vector` 與 `btree_gist`：

```bash
docker run -d --name env-law-dev-pg --restart unless-stopped \
  -p 127.0.0.1:5432:5432 -e POSTGRES_PASSWORD=postgres \
  -v env-law-dev-pg:/var/lib/postgresql \
  pgvector/pgvector:0.8.1-pg18
```

資料在具名 volume `env-law-dev-pg`，刪容器不會刪資料；要從零開始就 `docker rm -f env-law-dev-pg && docker volume rm env-law-dev-pg`。只綁 `127.0.0.1`，不對區網開放。

## 帳密與資料庫

以下全是本機值，不是機密；正式區的值都在叢集端建立，不進版控。

| 項目 | 值 | 出處 |
|------|----|------|
| 主機 PostgreSQL | `postgres`／`postgres` @ `localhost:5432` | 上面的 `docker run`；`appsettings.Development.json`、`scripts/worktree-db.py` 以此為準 |
| 主機資料庫（`wt`） | `platform_<branch>`、`ontology_<branch>` | `.config/wt.toml`；`wt` 起服務時建立，刪 worktree 時一併刪除 |
| 主機資料庫（直接 `dotnet run`） | `envlaw_platform` | `appsettings.Development.json`；platform 在 Development 會自建庫並遷移 |
| 內部 token | `dev` | 各服務 Development 設定、`wt` 別名、kind overlay |
| platform 營運者 key（`X-Admin-Key`） | kind：`admin`；主機直跑：未設定，`/admin` 停用 | kind overlay 的 `env-law-platform-admin`；主機要用就以環境變數 `Platform__AdminKeySha256` 給 key 的 SHA-256 hex（例如 `printf admin \| shasum -a 256`） |
| kind 資料庫 | `env_law`（ontology）、`platform`，擁有者皆為 `env_law` | `env-law-deploy` 的 `base/database/`、`base/platform/database.yaml` |
| kind 資料庫密碼 | CloudNativePG 建叢集時隨機產生，存於 `Secret/env-law-pg-app` | 不寫死；超級使用者存取未開啟（CloudNativePG 預設） |

連 kind 的資料庫（本機 5432 已被主機 PostgreSQL 佔用，故轉到 15432）：

```bash
kubectl --context kind-env-law -n env-law port-forward svc/env-law-pg-rw 15432:5432
kubectl --context kind-env-law -n env-law get secret env-law-pg-app -o jsonpath='{.data.password}' | base64 -d
# 以 env_law／上面的密碼連 localhost:15432，資料庫 env_law 或 platform
```
