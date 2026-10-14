# 環境知識 EnvCite

[![pipeline status](https://gitlab.com/thuaisse-lab/envcite/badges/main/pipeline.svg)](https://gitlab.com/thuaisse-lab/envcite/-/pipelines)

臺灣環境知識的檢索與引用接地（grounding）服務：把知識組織成可檢索的本體，並為呼叫端的語言模型備妥可核驗的封閉證據集；證據不足時，明確拒答。

本倉庫是單一倉庫（monorepo），各服務獨立建置、獨立部署。

## 目錄

- [`proto/`](proto/README.md)：gRPC 契約
- [`services/platform/`](services/platform/README.md)：控制平面與對外 REST API
- [`services/ontology/`](services/ontology/README.md)：本體，各來源知識的集中處
- [`services/consult/`](services/consult/README.md)：接地
- [`libs/python/`](libs/python/AGENTS.md)：共用套件

## 文件

- **本機開發環境**：[`CONTRIBUTING.md`](CONTRIBUTING.md)
- **API 與服務如何組合**：[`proto/README.md`](proto/README.md)
- **開發指引**：[`AGENTS.md`](AGENTS.md)；各服務另有巢狀 `AGENTS.md`

## 貢獻與回報

分支、MR 與合併流程見 [`CONTRIBUTING.md`](CONTRIBUTING.md)；問題與需求請開 GitLab issue。

## 授權

本倉庫為組織內部專案，不對外開源；程式碼與文件僅供組織內部使用。

法規資料取自[全國法規資料庫](https://law.moj.gov.tw)，依政府資料開放授權條款－第 1 版使用，須標示出處。
