# 臺灣環境知識檢索與引用接地服務 (envcite)

[![pipeline status](https://gitlab.com/thuaisse-lab/envcite/badges/main/pipeline.svg)](https://gitlab.com/thuaisse-lab/envcite/-/pipelines)

> 提供高可靠、可核驗的封閉證據集檢索與引用接地（Grounding）服務；在證據不足時明確拒答，防範模型幻覺。

---

## 服務架構與數據流

```mermaid
%%{init: {"flowchart": {"curve": "basis", "nodeSpacing": 45, "rankSpacing": 55, "padding": 20}}}%%
flowchart LR
    client(["<b>用戶端應用</b><br/>外部整合系統 / API 用戶"])
    llm{{"<b>語言模型 (LLM)</b><br/>企業或使用者專用端點"}}
    src[("<b>全國法規資料庫</b><br/>領域知識與法定依據")]

    subgraph envcite ["<b>EnvCite 核心服務叢集</b>"]
        direction TB
        platform["<b>platform 服務</b><br/>租戶鑑權 · 流量配額 · 呼叫計量 · REST 入口"]
        consult["<b>consult 服務</b><br/>引用接地 (Grounding) · 證據可答性判定"]
        ontology["<b>ontology 服務</b><br/>領域本體知識庫 · 語意檢索 · 邏輯推論"]
    end

    client ==>|"1. REST API (含 API Key)"| platform
    platform -->|"2. 呼叫接地判定 (gRPC)"| consult
    consult -->|"3. 查詢法規與本體證據 (gRPC)"| ontology
    platform -->|"管理與狀態同步 (gRPC)"| ontology
    src -->|"定時排程同步與匯入"| ontology

    client -.->|"4. 攜帶高信度證據集發問"| llm

    classDef client fill:#94a3b815,stroke:#94a3b8,stroke-width:1.5px
    classDef app fill:#6366f115,stroke:#6366f1,stroke-width:1.5px
    classDef data fill:#10b98115,stroke:#10b981,stroke-width:1.5px
    classDef ext fill:#06b6d415,stroke:#06b6d4,stroke-width:1.5px

    class client client
    class platform,consult,ontology app
    class src data
    class llm ext

    style envcite fill:#6366f108,stroke:#6366f1,stroke-dasharray: 3 3
```

---

## 核心服務劃分

| 服務模組 | 協定與職責 | 詳細文件 |
|----------|------------|----------|
| **`platform`** | 對外 REST API 閘道、API Key 授權驗證、租戶配額限流與計量統計 | [platform/README.md](services/platform/README.md) |
| **`consult`** | 引用接地核心引擎、證據充分性檢驗、封閉證據集建立與可答性防護 | [consult/README.md](services/consult/README.md) |
| **`ontology`** | 環境領域本體論模型、跨來源法規知識集中存儲、混合向量檢索與推論 | [ontology/README.md](services/ontology/README.md) |
| **`proto`** | 跨微服務內部 gRPC 介面定義契約 (Protocol Buffers) | [proto/README.md](proto/README.md) |
| **`libs/python`** | 內部通用 Python 套件（含 gRPC client/server 封裝、斷詞工具等） | [libs/python/AGENTS.md](libs/python/AGENTS.md) |

---

## 儲存庫結構

```text
envcite/
├── proto/                         # 微服務間 gRPC 接口定義
├── services/                      # 微服務核心實作
│   ├── platform/                  # API Gateway / 租戶管理 / REST 端點
│   ├── consult/                   # 引用接地與可答性邏輯
│   └── ontology/                  # 知識本體庫與檢索引擎
├── libs/                          # 共用內部套件庫
│   └── python/                    # Python 共用模組 (envcite-grpc, segmenter 等)
├── CONTRIBUTING.md                # 參與開發、分支與測試流程指引
├── AGENTS.md                      # AI 與開發者架構規範準則
└── README.md                      # 專案概覽與架構指引
```

---

## 開發與參考文件

- **參與開發**（本機開發環境、分支策略、MR 合併流程）：[`CONTRIBUTING.md`](CONTRIBUTING.md)
- **API 與服務契約**：[`proto/README.md`](proto/README.md)
- **架構規範與開發準則**：[`AGENTS.md`](AGENTS.md)（各微服務子目錄亦提供專屬規範）
- **部署配置**：由獨立的 GitOps 部署庫 [`envcite-deploy`](../envcite-deploy) 提供宣告式 Kubernetes 清單
