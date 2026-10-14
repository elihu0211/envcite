# AGENTS.md

Vendor-neutral instructions for coding agents. 倉庫層規範見根 `AGENTS.md`；本檔只管 `services/consult/`。

本檔只寫不會隨實作變動的規範與不變式；版本、預設值與清單以程式碼為準。

## 索引

| 文件 | 內容 |
|------|------|
| [`../../README.md`](../../README.md) | 倉庫總覽與目錄 |
| [`../../AGENTS.md`](../../AGENTS.md) | 倉庫層規範 |
| [`README.md`](README.md) | 本服務：介面、管線、啟動、開發指令 |
| [`../../proto/README.md`](../../proto/README.md) | 契約與呼叫端接法 |
| [`../ontology/`](../ontology/README.md)、[`../platform/`](../platform/README.md) | 上游與呼叫端，各有 README 與 `AGENTS.md` |

## Project

- **Name:** consult（`services/consult`；倉庫為 `envcite`）
- **Role:** 接地服務：以本體服務供給的內容，為**呼叫端的語言模型**備妥可引用的封閉證據集，或拒答。**本服務不執行生成模型。** **租戶隔離**——`x-tenant-id` 為必填，決定配額與稽核軌跡。
- **Stack:** Python · uv · gRPC（grpcio，同步伺服器，一元呼叫）· ruff · mypy (strict) · pytest（Python 版本見根 `.python-version`）
- **Interface:** `ConsultationService.Ground`（`envcite.consult.v2`）；本體 adapter 為 `adapters.GrpcOntology`（經 ontology 的 `SearchArticles`）；經 platform 的 `POST /v2/ground` 對外。映像：`Dockerfile`（context 為倉庫根；CI `consult-image` 在 main 推送 `:<sha>`）。

## 管線形狀（本服務存在的理由）

```mermaid
%%{init: {"flowchart": {"curve": "basis"}}}%%
flowchart LR
    subgraph here["consult（本服務）"]
        direction LR
        retrieve["<b>retrieve</b><br/>本體決定有什麼"]
        ground["<b>ground</b><br/>程式指派封閉引用集合<br/>L1、L2…"]
        assess{"<b>assess</b><br/>規則決定能不能答"}
        retrieve --> ground --> assess
    end

    subgraph caller["呼叫端"]
        direction LR
        generate["<b>generate</b><br/>模型寫散文<br/>只能以代號引用"]
        verify["<b>verify</b><br/>程式剝除模型自創的代號"]
        generate --> verify
    end

    refused(["refused<br/>拒答：是結果，不是錯誤"])
    assess -->|evidence| generate
    assess -->|證據不足| refused

    classDef own fill:#eef2ff,stroke:#6366f1,color:#1e1b4b
    classDef theirs fill:#f8fafc,stroke:#94a3b8,stroke-dasharray:4 3,color:#334155
    classDef stop fill:#fef2f2,stroke:#ef4444,color:#7f1d1d
    class retrieve,ground,assess own
    class generate,verify theirs
    class refused stop
```

**模型只決定一件事：如何把手上的證據寫成散文。** 其餘每個決定都在它跑之前由本服務做完，或在它回來之後由呼叫端核驗。這個形狀是契約的一部分，不是實作細節：

- **拒答是結果，不是錯誤。** `assess` 可以終止管線；呼叫端渲染它，不重試、不呼叫模型、不計為失敗。
- 代號由程式指派，永不由模型指派。`L3` 要嘛在清單裡，要嘛不在——這是引用可核驗的全部理由。
- 呼叫端的接法（引用方式、核驗）寫在倉庫根 `proto/README.md`，不寫在這裡。

## Commands

從**本目錄**執行。

```bash
uv sync --all-packages       # 整個 workspace（.venv 與 uv.lock 在倉庫根）
uv run python -m pytest
uv run ruff check .          # --fix 可自動修
uv run ruff format .
uv run python -m mypy src tests
../../libs/python/envcite-proto/scripts/generate.sh   # 改完根 proto/ 之後重新產生
ONTOLOGY_TARGET=dns:///localhost:<ontology 埠> uv run envcite-consult   # worktree 內用 `wt consult`
```

- 伺服器環境變數：`ONTOLOGY_TARGET`（必填，gRPC 目標）、`CONSULT_GRPC_PORT`（選用，預設值見 `server.py`）。服務間認證由叢集的服務網格（mTLS 加授權政策）負責，服務本身不驗證呼叫端。
- 測試不需要外部服務：本體以 fake 實作。**fake 不是次等實作**——管線的職責是決定拿到證據之後怎麼做，所以檢索本身正是測試該控制的部分。
- `ruff` 的 `RUF001`–`RUF003` 關閉：拒答訊息與 docstring 以中文書寫，全形標點是對的（理由見倉庫根 `pyproject.toml`）。

## Conventions

- 埠（`ports.py`）是 Protocol，不是用戶端。實作進 `adapters.py`。gRPC 產生碼（本服務的 `envcite.consult.v2` 與 ontology 的 client stubs）來自 `envcite-proto`。
- 門檻（`RELEVANCE_FLOOR`、`HIGH_CONFIDENCE_FLOOR`）是**模型條件化的常數**，不是領域常數：只在產生它的計分方式之下可比，該方式一改就必須重新量測分數分佈後重訂。
- 新增管線階段時，同時決定它「在模型之前或之後」——這個問題沒有第三個答案。在模型之前的屬本服務；在模型之後的屬呼叫端，本服務只能在契約與 `proto/README.md` 裡規定它。
- ontology 的 gRPC 狀態碼原樣轉出（`service.py`），不要吞成 `UNKNOWN`：呼叫端靠它分辨參數錯誤與可重試的不可用。

## Safety

- 不提交機密；設定一律由環境變數供給。
- 不要手改 `libs/python/envcite-proto/src/**/*_pb2*`（跑其 `scripts/generate.sh`）。
- 不要在本服務加入生成模型的呼叫：那會把模型選型、金鑰與按語彙單元計費帶回本服務，而它們已明確劃給呼叫端。

## Done when

- ruff、mypy、pytest 通過。
- 改了 proto 則兩側產生碼同一個 commit 更新，`proto/README.md` 同步。
