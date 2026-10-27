# 臺灣環境知識檢索與引用接地服務 (envcite)

[![pipeline status](https://gitlab.com/thuaisse-lab/envcite/badges/main/pipeline.svg)](https://gitlab.com/thuaisse-lab/envcite/-/pipelines)

臺灣環境知識的檢索與引用接地（Grounding）：為呼叫端的語言模型備妥封閉證據集，證據不足時拒答。服務如何組合、呼叫流程見 [`proto/README.md`](proto/README.md)。

| 路徑 | 職責 | 文件 |
|------|------|------|
| `services/platform/` | 對外 REST、API key 授權、租戶配額與計量 | [README](services/platform/README.md) |
| `services/consult/` | 引用接地：封閉證據集、可答性判定 | [README](services/consult/README.md) |
| `services/ontology/` | 本體：跨來源法規知識的集中存放、檢索與推論 | [README](services/ontology/README.md) |
| `proto/` | 服務間 gRPC 契約 | [README](proto/README.md) |
| `libs/python/` | 服務之外、可獨立測試的 Python 套件 | [AGENTS](libs/python/AGENTS.md) |

## 延伸閱讀

- 本機開發環境、分支與 MR 流程：[`CONTRIBUTING.md`](CONTRIBUTING.md)
- 架構規範與開發準則：[`AGENTS.md`](AGENTS.md)（各服務另有巢狀 `AGENTS.md`）
- 部署清單在獨立的部署庫，不在本倉庫
