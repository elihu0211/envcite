# env-law

環境法規 SaaS。單一倉庫，多個獨立部署的服務。

| 路徑 | 內容 |
|------|------|
| `proto/` | gRPC 契約（唯一真值） |
| `services/platform/` | 多租戶控制平面（C# · .NET 10） |
| `services/ontology/` | 法規範本體與檢索（Python · gRPC） |
| `docs/` | 學術專論庫（Quarto → PDF） |

開發指引見 [`AGENTS.md`](AGENTS.md)；各服務另有巢狀 `AGENTS.md`。

資料來源：全國法規資料庫（政府資料開放授權條款－第1版，需標示出處）。
