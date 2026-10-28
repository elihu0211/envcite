---
type: reference
applies-to: all
owns: [service-map]
---

# 臺灣環境知識檢索與引用接地服務 (envcite)

[![pipeline status](https://gitlab.com/thuaisse-lab/envcite/badges/main/pipeline.svg)](https://gitlab.com/thuaisse-lab/envcite/-/pipelines)

臺灣環境知識的檢索與引用接地（Grounding）：為呼叫端的語言模型備妥封閉證據集，證據不足時拒答。服務如何組合、呼叫流程見 [`proto/README.md`](proto/README.md)。

| 路徑 | 職責 | 文件 |
|------|------|------|
| `services/platform/` | 多租戶控制平面：對外 REST、API key 授權、租戶配額與計量 | [README](services/platform/README.md) · [AGENTS](services/platform/AGENTS.md) |
| `services/consult/` | 接地：封閉證據集、可答性判定，租戶隔離 | [README](services/consult/README.md) · [AGENTS](services/consult/AGENTS.md) |
| `services/ontology/` | 本體：各來源知識的集中存放、檢索與推論，不分租戶 | [README](services/ontology/README.md) · [AGENTS](services/ontology/AGENTS.md) |
| `proto/` | 服務間 gRPC 契約，唯一真值 | [README](proto/README.md) |
| `libs/dotnet/` | .NET 服務共用的主機設定：設定繫結、內部 gRPC 用戶端、遙測，不發佈 | [README](libs/dotnet/README.md) |
| `libs/python/` | 服務之外、可獨立測試的 Python 套件，uv workspace 成員，不發佈 | [AGENTS](libs/python/AGENTS.md) |

## 延伸閱讀

- 本機開發環境、分支與 MR 流程：[`CONTRIBUTING.md`](CONTRIBUTING.md)
- 架構規範與開發準則：[`AGENTS.md`](AGENTS.md)（各服務另有巢狀 `AGENTS.md`）
- 部署清單在獨立的部署庫，不在本倉庫
