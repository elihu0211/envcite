---
type: reference
applies-to: all
owns: [ontology-entrypoints, sync-exit-codes, models-config, norm-relations, competency-registry]
---

# envcite-ontology

環境知識本體的集中處：各來源的知識各成一個本體（如法規本體 `law`），在這裡提供語料、檢索與推論。回[倉庫總覽](../../README.md)。

- 介面：gRPC `OntologyService`（法規與檢索）、`ReasoningService`（標註與推論）；契約 [`ontology.proto`](../../proto/envcite/ontology/v1/ontology.proto)、[`reasoning.proto`](../../proto/envcite/ontology/v1/reasoning.proto)；約定與錯誤原因見 [`proto/README.md`](../../proto/README.md)。
- 呼叫端 [`platform`](../platform/README.md)、[`consult`](../consult/README.md)。資料全域共用，不分租戶；PostgreSQL 為唯一真值。
- 語料與檢索由 `envcite-sync` 依序取得、匯入、投影；標註由 `envcite-pipeline` 以語言模型抽取並經閘門驗證；推論（clingo）不用模型，只取已確認的標註，結論都是候選。
- 共用套件：[`envcite-segmenter`](../../libs/python/envcite-segmenter/README.md)、[`envcite-grpc`](../../libs/python/envcite-grpc/README.md)、[`envcite-proto`](../../libs/python/envcite-proto/README.md)。

## 入口

所有入口都在同一個映像裡：預設 command 是伺服器，其餘以 command 覆寫。入口名稱是與部署庫的契約，定義在 `pyproject.toml` 的 `[project.scripts]`。

| 入口 | 型態 | 做什麼 |
|------|------|--------|
| `envcite-ontology` | 長駐 | gRPC 伺服器 |
| `envcite-migrate` | 一次性 | 套用 `migrations/*.sql` |
| `envcite-sync <來源>` | 排程 | 取得、匯入、授權抽取、檢索投影，四步冪等 |
| `envcite-acquire <來源>` | 一次性 | 只取得快照 |
| `envcite-import <來源>` | 一次性 | 快照寫入語料；`--dry-run` 只解析與驗收 |
| `envcite-pipeline` | 排程 | 標註：抽取、驗證、接受 |
| `envcite-annotate <檔>` | 一次性 | 載入一份人寫的標註 JSON |
| `envcite-guidance <檔>` | 一次性 | 載入行政規則（函釋、裁量基準）JSON；格式見 [`guidance.py`](src/envcite_ontology/guidance.py) |
| `envcite-verify` | 一次性 | 只跑確定性閘門的驗證；`--require <方法,…>` |

`<來源>` 登記在 [`acquire/cli.py`](src/envcite_ontology/acquire/cli.py) 的 `SOURCES`；`moj` 是全國法規資料庫。開發用工具在 [`tools/`](tools)，不進映像，用法見各檔開頭。

**環境變數**：以 [`settings.py`](src/envcite_ontology/settings.py) 為準，範本是 `.env.example`。

`envcite-sync`、`acquire`、`import` 的結束碼：`0` 成功或無事可做，`1` 暫時性失敗（可重試），`2` 重試無益（資料不合預期，或嵌入端點拒絕認證、維度不符）。

**`envcite-sync` 的重試政策**：下載與嵌入對外部端點的重試次數、退避與斷路器，以 `ENVCITE_JOBKIT_<欄位>` 環境變數覆寫，欄位見 [`envcite-jobkit`](../../libs/python/envcite-jobkit/README.md#政策欄位)，預設值以 [`sync.py`](src/envcite_ontology/sync.py) 的 `POLICY` 為準。端點錯誤（連線、逾時、408、429、5xx）退避重試；嵌入的斷路器開啟時原地等到半開，重試用盡或內容被拒的批次跳過，留待下次同步補做，不使整次失敗。

## 標註管線

```bash
uv run --env-file .env envcite-pipeline --law <pcode> --models models.toml          # 指定法規
uv run --env-file .env envcite-pipeline --pending --limit <N> --models models.toml   # 自找待辦，最多 N 部
```

`--limit` 是每次的模型費用上限。驗證與推論的規範見 [`AGENTS.md`](AGENTS.md)；推論結果的呼叫端用法見 [`proto/README.md`](../../proto/README.md) 的〈標註與推論〉。

### 模型配置（`models.toml`）

端點、金鑰名稱、角色與嵌入模型只寫在 [`models.toml`](models.toml)。標註管線由 `--models` 指定，伺服器與同步由 `ENVCITE_ONTOLOGY_MODELS` 指向它。金鑰不寫進這個檔：端點的 `api_key` 只寫名稱，依序從同名環境變數、作業系統鑰匙圈解析，規則見 [`CONFIGURATION.md`](../../CONFIGURATION.md)。

```bash
uv run --env-file .env envcite-pipeline --models models.toml --check   # 對每個模型送探測問題，不連資料庫
```

`[embedding]` 只需要它指名的端點備妥金鑰，伺服器不因標註管線的端點缺金鑰而起不來。

## 規範關係：授權

命令依哪一部法律的哪一條訂定（行政程序法第 150 條第 2 項），是授權鏈的一環。授權是一種主張（`assertion_kind = delegation`，表 `annotation.delegation`），沿用推導活動、封存、驗證與確認狀態；其餘規範關係（準用、特別規定、引用）尚無表。

- **抽取**：`envcite-sync` 在匯入之後從語料內每部命令（位階非憲法、法律）的首條抽「本辦法依某法第某條規定訂定之」，純規則，`derivation.method` 為 `rule`，來源是 [`delegation.py`](src/envcite_ontology/delegation.py)；已有主張的引文（含駁回的）不重寫。其他來源（如公報說明的「依據：」）沒有抽取。
- **法律端**：法名只對語料內已有的法規標題（任一版本）且對到唯一一部；對不到、同名數部、或法規沒有該條文單元，`law_resource_id`／`law_provision_id` 為空，不猜。款記到所屬的項。
- **命令端**錨定（法規，條文單元，內容雜湊）：命令首條改了文字，授權跟著該日現行的文字走。授權不固定目標版本，不存版本，依查詢日解析。
- **讀取**（SQL 函式，唯讀角色可呼叫；`p_language` 同其他逐日解析）：
  - `annotation.delegations_at(命令法規 id, 語文, 日期)`：某命令在該日的授權依據（B1、B5）。
  - `annotation.delegated_by(法律法規 id, 語文, 日期)`：某法在該日的授權命令。
  - 兩者都回報法律端條文在該日的 `law_status`；`law_resource_id`／`law_provision_id` 為空、`law_status` 不是 `in_force`、或命令一列也沒有，都是授權不明，是否因此不得作為依據由呼叫端判斷。
- **守門**：只回已確認或規則推導的授權，駁回的不回；模型抽取而未確認的不回（同陳述與用語定義）。

## 違憲失效

大法官解釋與憲法法庭判決宣告條文違憲失效（能力問題 A5），記在 `corpus.constitutional_ruling`（裁判）與 `corpus.ruling_invalidation`（裁判使哪個條文單元自何日失效），不放進 `lifecycle_event`：匯入器重寫法規時會刪光它的事件，而來源是憲法法庭。

- **取得**：`envcite-ruling fetch`，走憲法法庭網站的解釋與判決清單逐則抓頁（來源只有 HTML，無 API；資料開放宣告允許重製與改作，**須標示出處**，實作在 [`acquire/cons.py`](src/envcite_ontology/acquire/cons.py)）。主文宣告失效的裁判連同原文入庫；失效條文由 [`ruling_extract.py`](src/envcite_ontology/ruling_extract.py) 的保守規則提出（句中恰有一個「語料裡的法規標題＋條（項）」、失效日寫法明確），寫法含糊的寧可不提——原文仍在庫，規則放寬（遞增 `EXTRACTOR_VERSION`）或語料新增法規後重跑即補上，不必重抓。頁面雜湊記在 `corpus.ruling_scan`，記過的頁面不再抓，`--rescan` 全部重抓。結束碼 0／1（網路等暫時性）／2（頁面不合或裁判內容與已載入的不同）。
- **載入**：`envcite-ruling load <file.json>`，格式見 [`ruling.py`](src/envcite_ontology/ruling.py) 的模組說明。由人讀解釋文填失效日，每則裁判附出處與解釋文原文；裁判不可改，同一字號內容須相同；一次載入一個交易。抽取流程只刪改自己寫的列（`extractor_version` 有值），人載入的列不動。
- **讀取**：rpc `OntologyService.ListInvalidations`（依法規、日期、可選的條號）；SQL 是 `corpus.invalidations_at(法規 id, 語文, 日期)`。只有裁判日已施行的文字被宣告失效，裁判之後修正的新文字不受影響。`partial` 標示裁判只宣告條文的一部分（失效仍記在整個條文單元，範圍看 `excerpt`）。
- **範圍與未做**：自動取得涵蓋全部大法官解釋與憲法法庭判決；裁定與不受理決議不取。規則只提得出明確寫法的失效（多條並列、「同法」回指、命令函釋、附條件的失效日都留給人以 `load` 填），所以**庫裡有裁判不等於已提出條文**。`provisions_at` 的 `status` 不含違憲失效，讀取方各自合併。事件所涉條文的通用表（A4）未做。

## 能力問題登記

本體必須回答的問題與各題今天的實作現況，只登記在 [`competency.toml`](competency.toml)：每題一列，`status` 是 `answerable`（術語層與機制都在且有測試）、`partial`（只做了一部分）或 `design_only`（只有設計，沒有承載）。「可答」說的是機制存在，不是資料庫裡已有答案。

每題的證據分四種：資料庫物件（`db`）、rpc（`rpc`）、契約欄位（`field`）與測試函式（`test`）。[`tests/test_competency.py`](tests/test_competency.py) 驗證：登記涵蓋題號集合、每項證據真的存在、標可答的題有機制與測試、標只有設計的題沒有證據。它不執行那些題，只驗證證據；加題或改現況時，登記檔與該測試裡的題號集合同一次更新。
