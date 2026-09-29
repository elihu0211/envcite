# AGENTS.md

Vendor-neutral instructions for coding agents. 倉庫層規範見根 `AGENTS.md`；本檔只管 `services/consult/`。

## Project

- **Name:** consult（`services/consult`；倉庫為 `env-law`）
- **Role:** 接地服務：以本體服務供給的法規事實，為**呼叫端的語言模型**備妥可引用的封閉證據集，或拒答。**本服務不執行生成模型。** **租戶隔離**——`x-tenant-id` 為必填，決定配額與稽核軌跡。
- **Stack:** Python 3.14 · uv · gRPC（grpcio，同步伺服器，一元呼叫）· ruff · mypy (strict) · pytest
- **Status:** `ConsultationService.Ground`（`env_law.consult.v2`）可運作；本體 adapter 為 `adapters.GrpcOntology`（經 ontology 的 `SearchArticles`）。經 platform 以 `POST /v1/ground` 對外開放。映像：`Dockerfile`（context 為倉庫根；CI `consult-image` 在 main 推送 `:<sha>`）。`env_law.consult.v1`（串流、含生成）已棄用，無實作。

## 管線形狀（本服務存在的理由）

```
retrieve   本體決定有什麼                    ┐
ground     程式指派封閉引用集合（L1、L2…）   ├ 本服務
assess     規則決定這題能不能答              ┘
generate   模型寫散文，只能以代號引用        ┐ 呼叫端
verify     程式剝除模型自創的代號            ┘
```

**模型只決定一件事：如何把手上的證據寫成散文。** 其餘每個決定都在它跑之前由本服務做完，或在它回來之後由呼叫端核驗。這個形狀是契約的一部分，不是實作細節：

- **拒答是結果，不是錯誤。** `assess` 可以終止管線；呼叫端渲染它，不重試、不呼叫模型、不計為失敗。
- 代號由程式指派，永不由模型指派。`L3` 要嘛在清單裡，要嘛不在——這是引用可核驗的全部理由。
- 呼叫端的接法（提示詞形狀、核驗程式）寫在倉庫根 `proto/README.md`，不寫在這裡。

## Commands

從**本目錄**執行。

```bash
uv sync --all-packages       # 整個 workspace（.venv 與 uv.lock 在倉庫根）
uv run python -m pytest
uv run ruff check .          # --fix 可自動修
uv run ruff format .
uv run python -m mypy src tests
../../libs/python/env-law-proto/scripts/generate.sh   # 改完根 proto/ 之後重新產生
CONSULT_INTERNAL_TOKEN=dev ONTOLOGY_TARGET=dns:///localhost:50051 ONTOLOGY_INTERNAL_TOKEN=dev uv run env-law-consult   # :50052；worktree 內用 `wt consult`
```

- 伺服器環境變數：`CONSULT_INTERNAL_TOKEN`（必填；呼叫端送 `authorization: Bearer <token>`）、`ONTOLOGY_TARGET`（必填，gRPC 目標，例如 `dns:///ontology:50051`）、`ONTOLOGY_INTERNAL_TOKEN`（必填）、`CONSULT_GRPC_PORT`（50052）。
- 測試不需要外部服務：本體以 fake 實作。**fake 不是次等實作**——管線的職責是決定拿到證據之後怎麼做，所以檢索本身正是測試該控制的部分。
- `ruff` 的 `RUF001`–`RUF003` 關閉：拒答訊息與 docstring 以中文書寫，全形標點是對的（理由見倉庫根 `pyproject.toml`）。

## Conventions

- 埠（`ports.py`）是 Protocol，不是用戶端。實作進 `adapters.py`。gRPC 產生碼（本服務的 `env_law.consult.v2` 與 ontology 的 client stubs）來自 `env-law-proto`。
- 門檻（`RELEVANCE_FLOOR`、`HIGH_CONFIDENCE_FLOOR`）是**模型條件化的常數**，不是領域常數：只在產生它的計分方式之下可比，該方式一改就必須重新量測分數分佈後重訂。
- 新增管線階段時，同時決定它「在模型之前或之後」——這個問題沒有第三個答案。在模型之前的屬本服務；在模型之後的屬呼叫端，本服務只能在契約與 `proto/README.md` 裡規定它。
- ontology 的 gRPC 狀態碼原樣轉出（`service.py`），不要吞成 `UNKNOWN`：呼叫端靠它分辨參數錯誤與可重試的不可用。

## Safety

- 不提交機密。`CONSULT_INTERNAL_TOKEN`、`ONTOLOGY_INTERNAL_TOKEN` 一律由環境變數供給。
- 不要手改 `libs/python/env-law-proto/src/**/*_pb2*`（跑其 `scripts/generate.sh`）。
- 不要在本服務加入生成模型的呼叫：那會把模型選型、金鑰與按語彙單元計費帶回本服務，而它們已明確劃給呼叫端（論據見 `env-law-docs` 的 00-01）。

## Done when

- ruff、mypy、pytest 通過。
- 改了 proto 則兩側產生碼同一個 commit 更新，`proto/README.md` 同步。
