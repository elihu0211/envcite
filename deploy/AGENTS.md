# AGENTS.md

倉庫層規範見根 `AGENTS.md`；本檔只管 `deploy/`。

## Commands

從 `deploy/` 執行：

```bash
kustomize build base                                             # 渲染
python3 scripts/extract-image-refs.py <(kustomize build base)    # 列出映像參照
```

## Conventions

- **上線是自動的**：env-law `main` 推出新映像後，Argo CD Image Updater（`argocd/image-updater.yaml`）改 Application，不需要改本目錄。tag 只用 commit SHA，不用 `latest` 或任何可變 tag。
- 一個部署單元一個 `base/<unit>/` 目錄；有第二個環境時才建 `overlays/<env>/`，在那之前不建。
- 順序靠 `argocd.argoproj.io/sync-wave`：資料庫 0 → 遷移 Job（Sync hook）1 → 服務 2。新增服務沿用。

## Safety

- 不提交機密：`Secret` 一律叢集端建立（見 README「叢集端前提」）。
- 動手前先問：改 `argocd/`、刪除資源（`prune: true` 會同步刪到叢集）、改資料庫 `Cluster`。

## Done when

- CI `deploy` job 通過：kustomize build、kubeconform strict、每個映像參照存在於 registry。
