---
type: reference
applies-to: all
owns: [ontology-entrypoints, sync-exit-codes, models-config, norm-relations, competency-registry, golden-test-collection]
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
| `envcite-history [sync\|acquire\|import]` | 排程 | 環境部主管法規的歷史版本：取得、回推、授權抽取、檢索投影；見〈歷史版本〉 |
| `envcite-pipeline` | 排程 | 標註：抽取、驗證、接受 |
| `envcite-annotate <檔>` | 一次性 | 載入一份人寫的標註 JSON |
| `envcite-guidance sync <來源>` | 排程 | 取得行政規則寫入 `guidance`（`gazette` 公報、`moenv` 環境部函釋）；見下節 |
| `envcite-guidance load <檔>` | 一次性 | 載入一份人寫的行政規則（函釋、裁量基準）JSON；格式見 [`guidance.py`](src/envcite_ontology/guidance.py) |
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

端點、角色、嵌入模型與相關性判定者只寫在 [`models.toml`](models.toml)。標註管線由 `--models` 指定，伺服器與同步由 `ENVCITE_ONTOLOGY_MODELS` 指向它。金鑰不寫進這個檔：金鑰名由端點名推導（`[endpoint.llm]` 的金鑰是 `ENVCITE_LLM_API_KEY`），依序從同名環境變數、作業系統鑰匙圈解析，規則見 [`CONFIGURATION.md`](../../CONFIGURATION.md)。

```bash
uv run --env-file .env envcite-pipeline --models models.toml --check   # 對每個模型送探測問題，不連資料庫
```

`[embedding]` 只需要它指名的端點備妥金鑰，伺服器不因標註管線的端點缺金鑰而起不來。`[relevance]` 是黃金測試集的相關性判定者（見「黃金測試集」），不參與任何管線。

## 規範關係：授權

命令依哪一部法律的哪一條訂定（行政程序法第 150 條第 2 項），是授權鏈的一環。授權是一種主張（`assertion_kind = delegation`，表 `annotation.delegation`），沿用推導活動、封存、驗證與確認狀態；其餘規範關係（準用、特別規定、引用）尚無表。

- **抽取**：`envcite-sync` 在匯入之後從語料內每部命令（位階非憲法、法律）的首條抽「本辦法依某法第某條規定訂定之」，純規則，`derivation.method` 為 `rule`，來源是 [`delegation.py`](src/envcite_ontology/delegation.py)；已有主張的引文（含駁回的）不重寫。其他來源（如公報說明的「依據：」）沒有抽取。
- **法律端**：法名只對語料內已有的法規標題（任一版本）且對到唯一一部；對不到、同名數部、或法規沒有該條文單元，`law_resource_id`／`law_provision_id` 為空，不猜。款記到所屬的項。
- **命令端**錨定（法規，條文單元，內容雜湊）：命令首條改了文字，授權跟著該日現行的文字走。授權不固定目標版本，不存版本，依查詢日解析。
- **讀取**（SQL 函式，唯讀角色可呼叫；`p_language` 同其他逐日解析）：
  - `annotation.delegations_at(命令法規 id, 語文, 日期)`：某命令在該日的授權依據（B1、B5）。
  - `annotation.delegated_by(法律法規 id, 語文, 日期)`：某法在該日的授權命令。
  - 兩者都回報法律端條文在該日的 `law_status`；`law_resource_id`／`law_provision_id` 為空、`law_status` 不是 `in_force`、或命令一列也沒有，都是授權不明。
- **推論**：命令陳述的授權鏈由 [`reasoning/facts.py`](src/envcite_ontology/reasoning/facts.py) 的 `chain_at` 沿 `delegations_at` 往上走到法律（授權者又是命令就接著走）；授權條文該日已違憲失效也算斷。鏈的每一環與缺口在 `Outcome.delegation_bases`；鏈到不了法律時，命令所定的罰則與處罰命令義務的罰則附條件，義務的結論不變。
- **守門**：只回已確認或規則推導的授權，駁回的不回；模型抽取而未確認的不回（同陳述與用語定義）。

## 行政規則的取得

`envcite-guidance sync <gazette|moenv> [--since YYYY-MM-DD] [--rescan]`，實作在 [`guidance_sync.py`](src/envcite_ontology/guidance_sync.py)。每個工作項（公報的一包、函釋的一頁）是一個 jobkit 工作項：端點錯誤退避重試、斷路器、單項隔離，政策以 `ENVCITE_JOBKIT_<欄位>` 覆寫，預設值以該模組的 `POLICY` 為準；每件公文一個交易，可重跑。

| 來源 | 取得 | 出處標示 | 錨定 |
|------|------|----------|------|
| `gazette` | 行政院公報開放資料的 ZIP（本月每日包與歷史月包），只取 `Doc_Style_LName` 為「行政規則」者 | 政府資料開放授權條款－第 1 版（資料集 5959） | 不錨定：XML 沒有相關法規欄 |
| `moenv` | 環境部主管法規查詢系統的函釋清單（走葉分類）與詳細頁；逐頁循序、間隔至少 0.5 秒 | 環境部網站資料開放宣告（CC BY 4.0） | 只取「相關法規」欄 |

- **略過已處理的**：公報看包名（`source.snapshot.origin_name`），函釋看發文字號是否已有來源項目；未給 `--since` 時公報從上個月一日起、函釋走全部清單。`--rescan` 全部重抓，用來發現網站內容的改變。
- **公文不可改**：同一件（發文機關，正規化字號）再寫入時內容不同，不改寫，記匯入問題 `content_changed`。字號去除空白與文別（「號函」記為「號」），公報與網站的同一件是同一列，先寫入者為準。
- **錨定**：法名對語料任一版本的標題且唯一，條號對到條文單元，錨定到該單元之下每個最細單元在**發文日**現行的文字；發文日沒有現行版本的不錨定。
- **匯入問題**（`source.import_problem`，鍵是來源項目）：`law_unresolved`、`law_ambiguous`、`no_article`、`provision_unresolved`、`no_version_at_issue`、`ceased_without_date`、`ceased_before_issue`、`content_changed` 為 warning；`kind_ambiguous`（公報標題同時像裁量基準與解釋）、`incomplete`、`rejected` 為 error，該件不寫入。
- **結束碼**沿用 jobkit（0、75、2、1）；隔離的工作項超過列舉數的 1% 時為 1。
- **未做**：函釋的下架偵測（連續兩次全量走訪未出現）、公報以標題「廢止」停止前一規則的對應、公報行政規則的錨定。

## 歷史版本

全國法規資料庫的 API 只有現行版本；環境部主管的法律與命令，每次修正前的完整版本取自環境部主管法規查詢系統（只有網頁；網站資料開放宣告 CC BY 4.0，**須標示出處**，實作在 [`acquire/moenv_law.py`](src/envcite_ontology/acquire/moenv_law.py)）。其他主管機關的法規沒有這個來源，版本只從首次取得全國法規資料庫快照起累積。

- **取得**：`envcite-history acquire` 走分類樹列出法律與命令，一部法規的歷史清單、現行頁與各歷史頁打包成一份快照（feed `moenv_law`，鍵是該系統的法規代碼）；已存的歷史頁不重抓，`--rescan` 全部重抓。
- **匯入**：`envcite-history import` 以名稱、種類對到語料的法規，主管機關系統的現行版本日要與語料最新版本同日；由語料最早的官方資料庫版本往舊回推（[`ingest/backfill.py`](src/envcite_ontology/ingest/backfill.py)），寫成 `text_authority = competent_authority` 的版本與條文單元版本。沿革的每一次公布都要有同日的全文，缺一版（或條文只在 PDF、解析不出）就停在那裡，更早的不寫；效力上界的規則同增量匯入，相鄰兩版同一單元的區間首尾相接。須先跑過 `envcite-sync moj`。
- **讀取**：不另立介面；`corpus.provisions_at` 與依日檢索照常逐條解析，歷史版本與官方資料庫版本同一條時間軸。
- 結束碼 0／1（暫時性，含隔離超過列舉數的 1%）／2（資料不合預期、整批比例越線）／75（斷路器開啟）。問題記在 `source.import_problem`。

## 違憲失效

大法官解釋與憲法法庭判決宣告條文違憲失效（能力問題 A5），記在 `corpus.constitutional_ruling`（裁判）與 `corpus.ruling_invalidation`（裁判使哪個條文單元自何日失效），不放進 `lifecycle_event`：匯入器重寫法規時會刪光它的事件，而來源是憲法法庭。

- **取得**：`envcite-ruling fetch`，走憲法法庭網站的解釋與判決清單逐則抓頁（來源只有 HTML，無 API；資料開放宣告允許重製與改作，**須標示出處**，實作在 [`acquire/cons.py`](src/envcite_ontology/acquire/cons.py)）。主文宣告失效的裁判連同原文入庫；失效條文由 [`ruling_extract.py`](src/envcite_ontology/ruling_extract.py) 的保守規則提出（句中恰有一個「語料裡的法規標題＋條（項）」、失效日寫法明確），寫法含糊的寧可不提——原文仍在庫，規則放寬（遞增 `EXTRACTOR_VERSION`）或語料新增法規後重跑即補上，不必重抓。頁面雜湊記在 `corpus.ruling_scan`，記過的頁面不再抓，`--rescan` 全部重抓。結束碼 0／1（網路等暫時性）／2（頁面不合或裁判內容與已載入的不同）。
- **載入**：`envcite-ruling load <file.json>`，格式見 [`ruling.py`](src/envcite_ontology/ruling.py) 的模組說明。由人讀解釋文填失效日，每則裁判附出處與解釋文原文；裁判不可改，同一字號內容須相同；一次載入一個交易。抽取流程只刪改自己寫的列（`extractor_version` 有值），人載入的列不動。
- **讀取**：rpc `OntologyService.ListInvalidations`（依法規、日期、可選的條號）；SQL 是 `corpus.invalidations_at(法規 id, 語文, 日期)`。
- **推論**：失效的版本自失效日起不是依據。`annotation.statement_versions_at` 不回讀它的陳述，`annotation.threshold_source_at` 不解析它的限值；被排除的陳述由 `annotation.invalidated_statements_at(法規 id, 語文, 日期)` 回報，進 `Outcome.invalidated` 與 `ListStatementsResponse.invalidated`，限值失效的要件附 `PROBLEM_KIND_INVALIDATED_DELEGATION`。`partial` 的裁判同樣整個排除。失效日是 `evaluate_across` 的變更日。只有裁判日已施行的文字被宣告失效，裁判之後修正的新文字不受影響。`partial` 標示裁判只宣告條文的一部分（失效仍記在整個條文單元，範圍看 `excerpt`）。
- **範圍與未做**：自動取得涵蓋全部大法官解釋與憲法法庭判決；裁定與不受理決議不取。規則只提得出明確寫法的失效（多條並列、「同法」回指、命令函釋、附條件的失效日都留給人以 `load` 填），所以**庫裡有裁判不等於已提出條文**。`provisions_at` 的 `status` 不含違憲失效，讀取方各自合併（推論與 `ListStatements` 已合併）。事件所涉條文的通用表（A4）未做。

## 黃金測試集

檢索的召回與拒答以外部黃金題庫（`golden.yaml`，不在倉庫）量測。工具在 `tools/`，指標與檢定由 [ranx](https://github.com/AmenRa/ranx) 算（只在開發依賴）；設計依據見 envcite-docs〈黃金測試集的架構〉。

| 步驟 | 指令（在本目錄） | 產出 |
|------|------|------|
| 建 qrels | `uv run python tools/golden_retrieval_eval.py qrels <golden.yaml> --out eval/golden-qrels.json` | qrels（需資料庫） |
| 切分 | `uv run python tools/golden_set.py split <golden.yaml> --out eval/golden-split.json` | 開發集與測試集 |
| 量測 | `uv run python tools/golden_retrieval_eval.py measure <golden.yaml> --qrels … --split … --subset dev --out eval/golden-retrieval-<日期>` | 報告 `.json`／`.md`、每臂一個 `.<臂>.run.json` |
| 比較 | `uv run python tools/golden_retrieval_eval.py compare --qrels … <a>.run.json <b>.run.json [--out …]` | 各 run 的指標、Hole@10、兩兩的 p 值 |
| 校準判定器 | `uv run python tools/relevance_judge.py calibrate <golden.yaml> --qrels … --split … --cache <倉庫外>/judge-cache.jsonl --out eval/golden-calibration-<日期>.json` | 開發集正例的召回、負例的誤判、κ（需資料庫與模型） |
| 模型判定 | `uv run python tools/relevance_judge.py pool <golden.yaml> --qrels … --split … --subset dev --run <a>.run.json [--run …] --calibration … --cache … --out eval/golden-judgements-<子集>-<日期>.json` | 封存的判定檔（需資料庫與模型） |
| 匯回判定 | `uv run python tools/golden_set.py judge --qrels eval/golden-qrels.json --judgements eval/golden-judgements-….json --out eval/golden-qrels.json` | 新版 qrels |
| 選下界 | `tools/answerability_floor.py`、`tools/floor_risk_coverage.py`，`--lexical`／`--semantic` 給開發集的報告 | 只印出，不改任何下界 |
| 端到端 | `tools/grounding_check_eval.py <golden.yaml> --qrels … --split … --subset test …` | 拒答與引用的報告 |

**格式**（JSON 文件都帶 `sha256`：自身去掉該欄後的正規化 JSON（鍵排序、緊湊分隔、UTF-8）的雜湊；`golden_set.read` 不符即停）：

- **條文代號**：`<pcode>-art-<條號>`，條號同 `corpus.article_number`（如 `O0020001-art-15-1`）。
- **qrels**：`qrels` 為題號 → 條文代號 → 分數 0–3（UMBRELA 的分級；`scale`）；分數 ≥ `relevant_from` 為相關，任何分數都是已判定。只含可答且對得到語料的題。`upstream` 是題庫給定的期望條文，以 `golden_set.UPSTREAM_GRADE` 進入，模型的分數只能把它往上調。`graded_by` 為題號 → 條文代號 → 判定檔的 `tag`（模型判定過的條）。另記 `golden_sha256`、對應時的語料快照 `snapshots`（量測時資料庫快照不同即停，要重建 qrels 再重新併入判定檔）、`unmapped`（題號 → 對不到的原因）與 `judgements`（每份併入的判定檔：檔名、雜湊、`tag`、判定者、日期、提示雜湊、子集、新增數）。無答案題不進 qrels。
- **切分**：`dev`、`test` 兩份題號清單（含無答案題），依桶分層、固定種子；記 `golden_sha256`、`seed`、`test_fraction`。選下界、調融合與候選池只用 `dev`；`test` 在定案後報告。
- **run**：`ranking` 為題號 → 前 k 條的條文代號（依名次）；記臂、`golden_sha256`、切分雜湊與子集、commit、語料快照、檢索設定。run 不綁 qrels，補判後可對新 qrels 重算。
- **報告**：`meta` 記 commit、題庫、qrels、切分與語料快照的雜湊；每臂的 `summary` 依桶列 `hit_rate@k`（表中的 R@k：任一相關條在前 k）、`mrr`、`ndcg@10`（分數為增益）、`hole@10`，`results` 逐題記狀態、第一個相關條的名次與兩臂最高分（不存題目文字）。
- **Hole@10**：每題前 10 條中 qrels 沒有判定（相關或不相關）的條所占的比例。
- **比較**：成對 Fisher 隨機化檢定（ranx `compare`，`stat_test="fisher"`），兩個 run 必須是同一切分子集、同一批題；p ≥ α 的差距不寫成改善或退步。
- **判定者**：模型配置檔的 `[relevance]`（`judges`、`system_families`）；至少兩個、彼此不同家族，且不與受測系統的家族相同（`system_families` 加上各角色模型的家族），判定前另以固定問題排除別名（`SameModel`）。回覆的模型名與所問的不同（閘道備援）視為失敗。判定模型不重排任何檢索結果。
- **判定**：提示是 UMBRELA 的 zero-shot 提示（意圖、M、T、O 四步、0–3 分）的繁體中文法規版，給題目、法規名稱與條號、`--as-of` 現行版本的公布或修正日與條文全文，溫度 0、以 JSON schema 要求 `intent`、`M`、`T`、`O`、`reason`。每條每位判定者判一次，最終分數取較低者；任一位沒有分數仍是洞。
- **校準檔**（`kind: calibration`，只在 `dev`）：題庫給定的條（正例）與每個正例一條同法規、題庫沒列給該題的隨機現行條（負例，固定種子）；`rates` 記各判定者與保守分數對正例的召回、對負例的誤判，`agreement` 記一致性。提示只依校準調；`pool` 要求校準檔的提示雜湊與現行提示相同，所以測試集只由在開發集定案的提示判定。
- **判定檔**（`kind: judgements`）：判一個子集各 run 前 10 條的洞與尚未分級的期望條文。記題庫、qrels、切分、各 run、校準檔的雜湊，提示全文與雜湊、`response_format`、判定者（名稱、模型識別、家族、溫度）、`as_of`、日期；`rows` 每列 `qid`、`doc`、`upstream`、`text_sha256`、各判定者的分數與 `verdicts`（含理由）、最終 `grade`、失敗原因；`agreement` 為二元化（≥ `relevant_from`）的 Cohen's κ、二次加權 κ、分歧比例與混淆表；`usage` 為各判定者的呼叫數與詞元。不存題目文字。快取（JSONL，含題目全文）放倉庫外，中斷可續跑。

## 能力問題登記

本體必須回答的問題與各題今天的實作現況，只登記在 [`competency.toml`](competency.toml)：每題一列，`status` 是 `answerable`（術語層與機制都在且有測試）、`partial`（只做了一部分）或 `design_only`（只有設計，沒有承載）。「可答」說的是機制存在，不是資料庫裡已有答案。

每題的證據分四種：資料庫物件（`db`）、rpc（`rpc`）、契約欄位（`field`）與測試函式（`test`）。[`tests/test_competency.py`](tests/test_competency.py) 驗證：登記涵蓋題號集合、每項證據真的存在、標可答的題有機制與測試、標只有設計的題沒有證據。它不執行那些題，只驗證證據；加題或改現況時，登記檔與該測試裡的題號集合同一次更新。
