# AGENTS.md

Vendor-neutral instructions for coding agents. 倉庫層規範見根 `AGENTS.md`；本檔只管 `services/consult/`。

## Project

- **Name:** consult（`services/consult`；倉庫為 `env-law`）
- **Role:** 諮詢服務：以本體服務供給的法規事實為據產出可反查引用的答案。**租戶隔離**——`x-tenant-id` 為必填，決定配額與稽核軌跡。
- **Stack:** Python 3.14 · uv · gRPC（grpcio，同步伺服器，server-streaming）· ruff · mypy (strict) · pytest
- **Status:** 管線骨架與契約已定，**無任何 adapter**——本體用戶端需要跑起來的服務，語言模型用戶端需要端點，兩者皆無。`main()` 因此直接失敗退出。

## 管線形狀（本服務存在的理由）

```
retrieve   本體決定有什麼
ground     程式指派封閉引用集合（L1、L2…）
assess     規則決定這題能不能答
generate   模型寫散文，只能以代號引用
verify     程式剝除模型自創的代號
```

**模型只決定一件事：如何把手上的證據寫成散文。** 其餘每個決定都在它跑之前做完或在它回來之後核驗。這個形狀是契約的一部分，不是實作細節：

- `grounding` 事件**必在**第一個 `answer_chunk` 之前送出——呼叫端能在散文存在之前就渲染出處，也才能拿一個已經在手上的集合去核驗散文裡的引用。
- **拒答是結果，不是錯誤。** `assess` 可以終止管線；呼叫端渲染它，不重試、不計為失敗。
- 代號由程式指派，永不由模型指派。`L3` 要嘛在清單裡，要嘛不在——這是引用可核驗的全部理由。

## Commands

從**本目錄**執行。

```bash
uv sync
uv run python -m pytest
uv run ruff check .          # --fix 可自動修
uv run ruff format .
uv run python -m mypy src tests
./scripts/generate-proto.sh  # 改完根 proto/ 之後重新產生
```

- 測試不需要外部服務：兩個埠都以 fake 實作。**fake 不是次等實作**——管線的職責是決定拿到證據之後怎麼做，所以檢索本身正是測試該控制的部分。
- `ruff` 的 `RUF001` 關閉：本服務以中文作答，字串字面值裡的全形標點是對的。`RUF002`／`RUF003` 保持開啟，故 docstring 與註解維持英文，與倉庫其餘部分一致。

## Conventions

- 埠（`ports.py`）是 Protocol，不是用戶端。實作進 `adapters.py`（尚未存在）。
- 門檻（`RELEVANCE_FLOOR`、`HIGH_CONFIDENCE_FLOOR`）是**模型條件化的常數**，不是領域常數：只在產生它的計分方式之下可比，該方式一改就必須重新量測分數分佈後重訂。
- 新增管線階段時，同時決定它「在模型之前或之後」——這個問題沒有第三個答案，而它決定該階段能不能被核驗。

## Safety

- 不提交機密。`CONSULT_INTERNAL_TOKEN` 與模型端點金鑰一律由環境變數供給。
- 不要手改 `src/env_law/consult/v1/*_pb2*`（跑 `generate-proto.sh`）。
- **不要讓 `main()` 在沒有 adapter 的情況下起得來**：一個起得來卻拒絕每一次呼叫的伺服器，看起來像部署好了，實際上答不出任何東西。

## Done when

- ruff、mypy、pytest 通過。
- 改了 proto 則兩側產生碼同一個 commit 更新。
