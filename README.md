# 環境知識 EnvCite

[![pipeline status](https://gitlab.com/thuaisse-lab/envcite/badges/main/pipeline.svg)](https://gitlab.com/thuaisse-lab/envcite/-/pipelines)

臺灣環境知識的檢索與引用接地服務：提供可核驗的封閉證據集，證據不足時明確拒答。

## 架構

```mermaid
%%{init: {"flowchart": {"curve": "basis", "nodeSpacing": 50, "rankSpacing": 70, "padding": 20}}}%%
flowchart LR
    client(["用戶端應用程式"])
    llm{{"自己的語言模型"}}
    src[("全國法規資料庫")]

    subgraph envcite["EnvCite"]
        platform["<b>platform</b><br/>租戶 · 限流 · 計量 · REST"]
        consult["<b>consult</b><br/>接地 · 可答性判定"]
        ontology["<b>ontology</b><br/>本體 · 檢索 · 推論"]
    end

    client ==>|"REST · API key"| platform
    platform -->|gRPC| consult
    consult -->|gRPC| ontology
    platform -->|gRPC| ontology
    src -->|"取得 · 匯入"| ontology
    client -.->|"證據集 + 提示詞"| llm

    classDef svc fill:#6366f120,stroke:#818cf8,stroke-width:2px
    classDef ext fill:#94a3b814,stroke:#94a3b8,stroke-dasharray:4 3
    classDef store fill:#10b98120,stroke:#34d399,stroke-width:2px
    class platform,consult,ontology svc
    class client,llm ext
    class src store
    style envcite fill:#6366f10d,stroke:#818cf8
```

## 目錄

| 路徑 | 內容 | 文件 |
|------|------|------|
| `proto/` | gRPC 契約 | [README](proto/README.md) |
| `services/platform/` | 控制平面與對外 REST API | [README](services/platform/README.md) · [AGENTS](services/platform/AGENTS.md) |
| `services/ontology/` | 本體，各來源知識的集中處 | [README](services/ontology/README.md) · [AGENTS](services/ontology/AGENTS.md) |
| `services/consult/` | 接地 | [README](services/consult/README.md) · [AGENTS](services/consult/AGENTS.md) |
| `libs/python/` | 共用套件 | [AGENTS](libs/python/AGENTS.md) · [envcite-grpc](libs/python/envcite-grpc/README.md) · [envcite-proto](libs/python/envcite-proto/README.md) · [envcite-segmenter](libs/python/envcite-segmenter/README.md) |

## 文件

- **參與開發**（本機環境、分支、MR 與合併流程）：[`CONTRIBUTING.md`](CONTRIBUTING.md)
- **API 與服務如何組合**：[`proto/README.md`](proto/README.md)
- **開發指引**：[`AGENTS.md`](AGENTS.md)；各服務與 `libs/python/` 另有巢狀 `AGENTS.md`
- **部署與上線**：[`envcite-deploy`](https://gitlab.com/thuaisse-lab/envcite-deploy)（Kubernetes 清單，Argo CD 的唯一來源）
