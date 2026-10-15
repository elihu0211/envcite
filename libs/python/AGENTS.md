# AGENTS.md

倉庫層規範（Layering、Contract、Safety、buf）見根 `AGENTS.md`；本檔只管 `libs/python/`。

## 索引

| 文件 | 內容 |
|------|------|
| [`../../README.md`](../../README.md) | 倉庫總覽與目錄 |
| [`../../AGENTS.md`](../../AGENTS.md) | 倉庫層規範（含「何時才抽出共用套件」的判準） |
| [`envcite-grpc/`](envcite-grpc/README.md) | 兩個以上服務共用的 gRPC 管線零件 |
| [`envcite-proto/`](envcite-proto/README.md) | `proto/` 的 Python 產生碼 |
| [`envcite-segmenter/`](envcite-segmenter/README.md) | 中文斷詞，lexicon 由語料產生（無第三方依賴） |

## Project

- 這裡的每個套件都是倉庫根 uv workspace 的成員，**不發佈**；使用端以 `[tool.uv.sources] <name> = { workspace = true }` 引用。
- **只放已經有第二個消費端的東西**（獨立依賴與測試週期的函式庫可一開始就放）；判準見根 `AGENTS.md` 的「目錄配置與擴充判準」。
- 依賴版本不在套件裡寫：加依賴用 `uv add --raw <pkg>`，版本在根 `uv.lock`。

## Commands

從倉庫根或各服務目錄執行同一個 workspace 的指令：

```bash
uv sync --all-packages
uv run python -m pytest <套件路徑>
uv run ruff check <套件路徑>
uv run python -m mypy <套件路徑>/src
```

工具設定在根 `pyproject.toml`。CI 依改動路徑分流，見 [`CONTRIBUTING.md`](../../CONTRIBUTING.md)。使用這些套件的服務：[ontology](../../services/ontology/AGENTS.md)、[consult](../../services/consult/AGENTS.md)。

## Conventions

- **`envcite-proto` 是產生物**：`src/**/*_pb2*` 不手改；改了 `proto/` 之後跑其 `scripts/generate.sh`，產生碼與 `proto/` 同一個 commit 更新（CI 會擋不一致）。`uv lock --upgrade` 若升級了產生器或 protobuf，產生碼隨之過期，要重新產生並一起提交。
- **`envcite-segmenter`：索引端與查詢端必須用同一個分詞器**，兩端不可能用到不同的東西是這個套件存在的理由。分詞器識別碼含詞典雜湊，詞典變動會讓既有索引與新查詢對不上——這是刻意的，處理方式是重建索引，不是放寬比對。量測方法在套件的 [`docs/`](envcite-segmenter/docs/segmentation-measurement.md)。
- **`envcite-grpc`：連線選項成套**——伺服器的最短 ping 間隔必須小於客戶端的 keepalive 間隔，改一邊要看另一邊（測試守著這個不等式）；平台端的對應設定見 [`services/platform/AGENTS.md`](../../services/platform/AGENTS.md) 的 Contract（`InternalGrpcClient`）。
- 新增套件時，同一次提交更新本檔的索引、根 `README.md` 的目錄，以及 CI 的路徑分流。

## Safety

- 不提交機密。
- 產生物與鎖定檔不手改：`envcite-proto/src/**/*_pb2*`、根 `uv.lock`。

## Done when

- 受影響套件與使用它的服務的 ruff／mypy／pytest 通過。
