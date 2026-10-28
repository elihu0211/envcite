# AGENTS.md

倉庫層規範（Layering、Contract、Safety、buf）見根 `AGENTS.md`；本檔只管 `libs/python/`。

套件：[`envcite-grpc`](envcite-grpc/README.md)、[`envcite-jobkit`](envcite-jobkit/README.md)、[`envcite-proto`](envcite-proto/README.md)、[`envcite-segmenter`](envcite-segmenter/README.md)。

## Project

- 這裡的每個套件都是倉庫根 uv workspace 的成員，**不發佈**；使用端以 `[tool.uv.sources] <name> = { workspace = true }` 引用。
- 何時才放進這裡，依根 `AGENTS.md` 的「目錄配置與擴充判準」。

## Commands

從倉庫根或各服務目錄執行同一個 workspace 的指令：

```bash
uv sync --all-packages
uv run python -m pytest <套件路徑>
uv run ruff check <套件路徑>
uv run python -m mypy <套件路徑>/src
```

工具設定在根 `pyproject.toml`。使用這些套件的服務：[ontology](../../services/ontology/AGENTS.md)、[consult](../../services/consult/AGENTS.md)。

## Conventions

- **`envcite-proto` 是產生物**，不手改；重新產生與升級的規則見其 README。
- **`envcite-segmenter`**：讓索引端與查詢端不可能用到不同的分詞器，是這個套件存在的理由；不一致時重建索引，不放寬比對。識別碼與選型見其 README 與 `docs/`。
- **`envcite-grpc`：連線選項成套**，改一邊要看另一邊；不等式與兩端位置見其 README。
- **`envcite-jobkit`：只用標準庫**，不加第三方依賴（Job 映像的依賴面要小）。結束碼是與部署庫的契約（數值見其 README），不改。
- 新增套件時，同一次提交更新本檔的套件清單、根 `README.md` 的目錄，以及 CI 的路徑分流。

## Done when

- 受影響套件與使用它的服務的 ruff／mypy／pytest 通過。
