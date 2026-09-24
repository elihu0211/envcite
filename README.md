# env-law

環境法規 SaaS。單一倉庫，多個獨立部署的服務。

| 路徑 | 內容 |
|------|------|
| `proto/` | gRPC 契約（唯一真值） |
| `services/platform/` | 多租戶控制平面（C# · .NET 10） |
| `services/ontology/` | 法規範本體與檢索（Python · gRPC） |
| `services/consult/` | 接地問答（Python · gRPC streaming） |
| `libs/python/` | 服務間共用的 Python 套件 |

開發指引見 [`AGENTS.md`](AGENTS.md)；各服務另有巢狀 `AGENTS.md`。設計論據在同層的 `env-law-docs` 倉庫；Kubernetes 清單（Argo CD 來源）在同層的 `env-law-deploy` 倉庫。

資料來源：全國法規資料庫（政府資料開放授權條款－第1版，需標示出處）。
