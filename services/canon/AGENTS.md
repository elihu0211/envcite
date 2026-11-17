# AGENTS.md

倉庫層規範見根 [`AGENTS.md`](../../AGENTS.md)；本檔只管 `services/canon/`。

## Project

### 服務定位與命名

- **Name:** `canon`（典籍：具權威的條文與典籍全體，對應 PostgreSQL 為唯一真值、結論只是候選的定位）。
- **服務名出現處**：目錄、Python 套件與映像、叢集服務與工作負載、指令前綴 `canon-`、環境變數 `ENVCITE_CANON_*`、可觀測性服務名（不加專案名 `envcite`）。
- **Naming**：持久化與錯誤識別碼前綴同為 `canon`：
  - 資料庫角色：`canon_reader`／`canon_writer`／`canon_reviewer`（遷移檔 `0027`–`0067` 與 `0068` 內的舊角色名因檢查碼不改；`0068` 之後一律用新角色名）
  - 計量鍵 `canon.*`、API key 範圍 `canon:*`、結果碼 `canon.*`、`ErrorInfo.domain` 為 `canon.envcite`
  - gRPC 套件 `envcite.canon.v1`（`CanonService`、`ReasoningService`）：改動為破壞性變更，須開新套件版本
- **Role**：知識服務（shared, non-tenant data），由 `services/platform` 與 `services/consult` 呼叫。一種知識來源對應一個來源子包，各自擁有 schema 與引用代號字母（呼叫端規則見 [`proto/README.md`](../../proto/README.md)）；不因新增來源另立服務，除非需要獨立擴縮、發版、資料隔離或執行環境。

### 架構分層與依賴方向

依賴只往單一方向：**來源子包使用核心，核心不匯入任何來源子包**，來源之間不互相匯入，僅組合根負責接線（由 [`tests/test_architecture.py`](tests/test_architecture.py) 守門）。

| 層級 | 路徑 | 職責 |
|------|------|------|
| 組合根 | `cli.py`、`server.py`、`sync.py`、`acquire/cli.py` | 解析設定、建立連線、將來源與實作注入核心 |
| 核心：介面與轉換 | `service.py`、`endpoints/`、`reasoning_service.py`、`reasoning_codec/`、`errors.py` | gRPC Servicer 邊界、錯誤映射與型別轉換 |
| 核心：資料存取 | `store/`（`ports.py`、`canon_store.py`、`laws`、`graph`、`retrieval`、`connections`） | 以 Protocol 隔離儲存庫，PostgreSQL 讀取與連線池 |
| 核心：檢索與共用 | `acquire/`、`projection.py`、`embedding.py`、`reranking.py`、`guidance_search.py`、`law_names.py`、`coverage.py`、`clock.py`、`import_problems.py` | 通用抓取引擎、混合檢索、精排、時鐘與問題記錄 |
| 核心：標註與推論 | `verification/`、`claims/`、`annotate.py`、`reasoning/` | 抽取／驗證管線、標註交易寫入、clingo ASP 推論與裁罰 |
| 來源子包 | `law/`（新增來源放同層 `canon/<代號>/`，不建 `sources/` 容器） | 法規與行政規則的取得、解析、歷史回推與同步 |

### 技術棧與資料真值

- **Stack**：Python（≥3.14）· uv · gRPC（`grpcio` 同步伺服器）· PostgreSQL（`psycopg` 3）· ruff · mypy (strict) · pytest
- **唯一真值**：PostgreSQL 關聯式實表，表結構以 `migrations/*.sql` 為準（不修改已套用的遷移檔，由 `schema_migration` 雜湊強制）。
- **法規主鍵與時間軸不變式（由綱要約束強制）**：
  - 自然鍵為 `(jurisdiction, id_scheme, local_id)`，三欄皆無預設值，方案與管轄配對由 CHECK 強制（地方方案目前僅為契約預留）。
  - 效力逐條文單元記載與解析；名稱可隨修正而變，標題屬公布版本；標註錨定 `(單元, 內容雜湊)`，不錨定版本列。
  - 版本時間軸不重疊（`WITHOUT OVERLAPS`）、不留縫（上界以延遲外鍵參照下一版版本日）、施行規則由 CHECK 強制（早於版本日僅限明文 `retroactive`）；缺全文版本仍佔一版（`text_status = unavailable`）。

## Commands

Python 一律經 `uv`，從**本目錄**執行：

```bash
uv sync --all-packages       # 整個 workspace（.venv 與 uv.lock 在倉庫根）；標註管線用 Anthropic 模型時加 --extra llm
uv run python -m pytest
uv run ruff check .          # --fix 可自動修
uv run ruff format .
uv run python -m mypy src tests
../../libs/python/envcite-proto/scripts/generate.sh   # 改了 proto 之後重新產生 gRPC 程式碼
docker build -f Dockerfile ../..   # 映像（context = 倉庫根）

# 本機執行（環境裡已有的同名變數優先於 .env）
cp .env.example .env
uv run --env-file .env canon migrate
uv run --env-file .env canon sync moj
uv run --env-file .env canon serve
```

### 程式風格與守門規則

- **命名與佈局**：發行名、匯入名與服務名同為 `canon`（`src/` 佈局）。子指令登記在 [`src/canon/cli.py`](src/canon/cli.py) 的 `SUBCOMMANDS`，每個子指令須可解析且 `--help` 可用（`tests/test_cli_entry_points.py` 守門）。禁用 `utils`、`helpers` 等泛稱，不與標準庫同名（ruff `A005`）；領域例外依事件命名（不套 `N818`）。
- **Python 語法與型別**：
  - 不寫 `from __future__ import annotations`；泛型用 PEP 695（`def f[T]`），別名用 `type X = …`。
  - 函式大小上限由 ruff 守門（圈複雜度與分支 ≤ 14、語句 ≤ 60），不加 `noqa` 放寬；單一模組不超過 500 行（`tests/test_architecture.py` 守門）。
  - 公開模組、類別與函式須有 docstring（`D100`–`D103`）：首句說明「是什麼」，其餘只寫前提、不變式與「為什麼」；欄位與常數用前置 `#:`。
  - 禁用 `Any`（`ANN401`），動態邊界（JSON 文件、clingo 答案集原子、唯讀連線列）例外須在根 `pyproject.toml` 列出並說明；人寫 JSON 在邊界以 pydantic 解碼成型別化結構（參照 `law/ruling.py`）。
- **日誌**：只由 `cli.main` 設定一次（`server.py` 因要記錄器名稱另設），子指令不呼叫 `logging.basicConfig`（`tests/test_architecture.py` 守門）。
- **資料庫讀取與組態**：
  - 查詢用 `db.fetch`／`fetch_one`／`scalar` 搭配私有 `NamedTuple`（欄位次序同 `SELECT`，可空欄標 `| None`），不於用處 `cast`；欄位次序同 `store/records.py` 時直接共用紀錄型別。連線型別只用 `db.Conn`。
  - 匯入問題（`source.import_problem`）的型別與寫入只經 `import_problems`。
  - 邏輯不直接讀系統時鐘（`date.today()`、`datetime.now()`），須收注入的 `clock.Today`；`today` 查詢語意由 SQL `as_of` 決定。
  - 環境變數以 [`src/canon/settings.py`](src/canon/settings.py) 為唯一來源，新增時同步 `.env.example`（`tests/test_settings.py` 守門）；指令列 `main` 是唯一可呼叫 `psycopg.connect` 與 `settings.load` 的組合根。

### 模組職責分層

套件 `__init__.py` 只放套件說明、不匯出名稱，一律從定義該符號的子模組匯入（`tests/test_architecture.py` 守門）。

| 子套件 | 職責拆分 |
|------|------|
| `store/` | `laws`、`graph`、`retrieval` 各管一類儲存庫，`connections` 管連線池與期限，`CanonStore` 接線；消費端只認 `store/ports.py` 的 Protocol |
| `endpoints/` | 按功能分 `laws`、`graph`、`search`，只依賴所需 Protocol；`service.CanonServicer` 僅於邊界轉交並以 `errors.maps_errors` 映射錯誤 |
| `reasoning_codec/` | 按轉換方向分 `request`、`outcome`、`disposition`、`enums` |
| `reasoning/facts/` | 陳述端讀 DB（`statements`、`chains`、`condition_facts`、`vocabulary_facts`、`relation_facts`，各自持有 SQL 與列型別），案件端讀請求（`case_inputs`），共用 ASP 寫法在 `asp` |
| `reasoning/scoring/` | `models`（規格與結果型別）、`formulas`（三種公式）、`adjustments`、`evaluate`、`matrices`、`audit` |
| `claims/` | `loader`（入口與交易邊界）、`statements`、`sanctions`、`links`、`definitions`、`vocabulary`、`held`、`anchoring`、`rows`（共用寫入原語） |
| `verification/` | `extract.py` 只串接流程；確定性檢查按類分置 `checks/`（`locate`、`fragments`、`sanctions`、`shape`、`coverage`）；模型閘門在 `model_gates/`（`roundtrip`、`independent`、`panel`、`render`、`common`）；主流程 `run.py` 拆出 `selection`、`methods`、`attempts`、`distinct`；提示詞在 `templates/*.j2` 經 `prompts.py` 渲染 |
| `law/backfill/` | `models`、`anchors`、`planner`（`plan` 純函式與 `texts`、`intervals`）、`writer`（一部一交易） |
| `law/history_sync/` | `run.sync` 串接 `acquire` 與 `importing`、`policy`（重試與整批驗收上限）、`report`、`cli` |
| `law/guidance_sync/` | `run.sync` 串接 `listing`、`job`、`settle`（函釋下架）、`repeals`（公報廢止）、`cli` |

### 資料庫角色與測試

- **資料庫角色**：`reader`（伺服器）、`writer`（取得、匯入、投影、標註管線）、`reviewer`（`writer` 加手動審核與載入已審核主張）。標註層對寫入者為 append-only：內容寫入後不改不刪，驗證紀錄經 `record_verification`、接受經 `settle`、手動審核經 `set_confirm_state`（皆 `SECURITY DEFINER`）。
- **測試環境**：須使用真實 PostgreSQL（設 `ENVCITE_CANON_TEST_DSN` 或留空由 testcontainers 啟動，不使用替身）；權限測試用 `SET ROLE`。放寬歷史回推測試上限用 `tests/history_support.relax_limits`。

## 語料管線

### 1. 取得與匯入

- **取得引擎（`acquire/`）**：負責下載、SHA-256、冪等與存檔；來源只實作 `Source` 並於 `acquire/cli.py` 的 `SOURCES` 登記。
- **增量匯入**：`parsing.parse` 為純函式，整批通過 `audit.LIMITS` 才寫入（否則結束碼 `2`）。更正以新增列取代舊列（`superseded_at`），查詢只看現行列。**改變解析輸出須遞增 `parsing.PARSER_VERSION`**。
- **確定性規則（授權、違憲引用等）**：寫成 `rules.Extractor`（`name`、整數 `version`、冪等的 `extract`），在 `sync.EXTRACTORS` 登記，由 `canon sync` 匯入後經 `rules.run` 依序執行；**改解析規則須遞增該規則的 `version`**（`delegation.RULE_VERSION`、`violation.RULE_VERSION`）。載入文件的規則沒有 `extract`，用 `rules.Rule`，`derivation` 文件取 `Rule.derivation`，不手寫字典。資料庫存成文字的版本欄位以 `str()` 寫入，不為統一型別而改欄位。
- **結束碼**：`0` 成功；`1` 暫時性錯誤；`2` 資料形狀不合、快照不存在、整批比例越線或嵌入端點設定錯誤（401／403、維度不符）。數值只取自 `envcite_jobkit.ExitCode`，入口不自訂數字（`tests/test_architecture.py` 守門）；例外自帶碼（`exit_codes.Retryable`、`Unretryable`），失敗收尾用 `exit_codes.fail`（回滾、記一行、回傳碼）。

### 2. 歷史版本與行政規則

- **歷史版本（`canon history`）**：一部法規單一來源（`law/history_sources.py` 決定：法律自 `ly`、環境部命令自 `moenv`）。由最早官方資料庫快照往舊逐版回推，缺全文處寫為 `text_status = unavailable`（不跳過、不以他版頂替）；與較新版正規化後相同的條沿用較新版文字。每次重算只寫差異；已寫入版本日不在新目標內時整部不寫（`history_reshaped`）。
- **行政規則（`guidance` schema）**：函釋與裁量基準另立一軸，**不進語料位階、不是推論前提**（行政程序法第 159 條），僅附於推論結果旁（`Outcome.guidance`）。公文不可改，只由人或規則載入（`derivation.method` 不得為 `llm`）；解釋錨定 `(單元, 內容雜湊)`。**改變寫入內容或錨定規則須遞增 `guidance_sync.provenance.RULE_VERSION`**。

### 3. 混合檢索、函釋入口與精排

檢索詞典、法規名稱表（`law_names.LawNames`）與分詞器（受 `envcite-segmenter` 約束）於伺服器啟動時讀一次，**語料或詞典重建後須重啟服務**。調校任何檢索／門檻參數前，須以 `tools/golden_retrieval_eval.py` 在開發集量測並以 `compare` 檢定，不憑感覺調整。

- **混合雙臂與查詢詞覆蓋率**：字面臂與語意臂同列（無嵌入設定時只用字面臂），以段落雜湊為鍵。
  - `retrieval.prune_terms` 於查詢前略過段落比例超過 `ENVCITE_CANON_LEXICAL_MAX_DF` 的高頻詞（全為高頻詞時保留最稀有的一個）；`retrieval.term_stat` 隨投影整批重算。
  - `SearchArticles` 另回 `term_coverage`（`coverage.term_coverage`），**不得將覆蓋率乘進 `lexical_score`**，以免破壞上游相關性下界尺度。
- **問句點名法規縮小範圍**：問句含三字以上不重疊法規全名時，`search_hybrid` 先以 `MATERIALIZED` 的 `scope` CTE（`retrieval.law_scope` 沿授權鏈展開）限縮範圍（語意臂改精確掃描）；無命中時才退回全庫。命中義務條時，`retrieval.search_expanded` 依 `annotation.current_violation` 帶出處罰條。
- **行政法院裁判（`judgment` schema，[`law/judgment.py`](src/canon/law/judgment.py)）**：與函釋同屬解釋依據，不是推論前提、不改條文效力區間；錨定 `(單元, 內容雜湊)`，但錨點可以是舊版本文字（裁判適用行為時或裁處時的條文），`basis` 記錄版本是裁判載明（`stated`）還是以裁判日代替（`decided_on`，須為裁判日現行）。以 jid 為鍵，同一 jid 再出現即整筆取代；只收行政類。**撤下是刪除不是狀態轉移**：來源回應「查無資料」時 `take_down` 刪內容與錨定邊，只留 jid 與偵測日的墓碑；日後依裁判而生的衍生資料（索引、向量、摘要）外鍵必須 `ON DELETE CASCADE` 到 `judgment.decision`（`tests/test_judgment.py` 守門）。只由人或規則載入。理由抽引用與版本判定在 [`law/judgment_anchor.py`](src/canon/law/judgment_anchor.py)：只取適用／依／違反句型，比較或援引整句不取；「修正前」「行為時」「裁處時」只在對上唯一版本時錨定，否則該引用不建邊。**改變抽取或版本規則須遞增 `judgment_anchor.RULE_VERSION`**。每晚同步（`canon judgment sync`）只在臺灣時間 0 時至 6 時取 `JList`／`JDoc`：先消化 `judgment.fetch` 的待取列，再要新清單；非行政類不入列。帳密只從密鑰檔讀（叢集 `/var/run/canon/judicial.json`，否則服務目錄的 `judicial.local.json`；其他人不可讀，`0600` 與 `0440` 皆可），不從環境變數讀，不入日誌。推論以裁處日經 `judgment.for_statements` 附在結果旁（`Outcome.court_decisions`），不參與結論。法規語料的焦點法規經 `GetLawGraph` 的 `court_decisions` 列出同一件事，不進圖譜節點。
- **函釋帶出條文（`include_guidance`）**：`retrieval.guidance_articles` 比對查詢詞組（最多 5 個）並以倒數名次融合（最多多回 5 條）與回傳 `guidance_documents`；若命中函釋占可用函釋比例超過 `ENVCITE_CANON_GUIDANCE_MAX_DF` 則整個查詢停用此入口。函釋只用來找現行條文，不作推論前提。
- **精排（`reranking.py`、`service._rerank`）**：帶 `rerank` 且已設定 `[reranker]` 時，取 `ENVCITE_CANON_RERANK_CANDIDATES` 條候選送 `/rerank` 重排（依 `index` 還原次序、不送空清單、每批上限 `MAX_BATCH = 30` 共用時間預算）。
  - **降級契約**：精排為加強而非成功條件。`RERANK_SLOTS` 滿、逾時、端點錯誤或回應缺漏重複時，整份退回精排前次序且不設 `rerank_score`，不重試、不回錯誤；函釋帶出被擠出的條文仍依 `fuse` 契約補回。
  - **指標**：僅記錄 `canon.rerank.outcomes`（`reranked`、`busy`、`error`、`unconfigured`）與 `canon.rerank.duration`（`ok`、`error`），成功時不寫日誌。
- **原生題庫（`tools/native_golden.py`）**：題目與答案純由條文結構程式化產生，不依賴模型或受測系統。改產生規則須遞增 `GENERATOR_VERSION` 並重建與 `derive`；不可為讓 `verify` 通過而放寬或收緊文法。

## 標註、驗證與推論

### 1. 抽取與形狀修復

- **無人工審核**：陳述以 `extracted` 進入，經閘門後依政策升為 `confirmed`，**只有已確認（或規則推導）的陳述參與推論**。
- **形狀與語意邊界**：
  - 抽取者輸出不可信，形狀問題（如規範句引文缺助動詞、引文停在並列行為前）送修復迴圈，修不好只丟該陳述，不連累同單元其餘陳述；義務引文漏涵蓋期限／對象／限定語或借用本法泛稱（約定 G）送修復但不丟棄。新增檢查須放進 `verification/checks/` 並接進修復迴圈。
  - **主體與上位條件由程式決定**（`verification/subject.py`、`carry.py`）：主體非原文行為人時記為 `statement_slot.unstated`；上位條件子句未涵蓋則補為 `statement_condition.carried`（概念代碼 `ctx_` 加原文雜湊）。兩者形狀由 DB CHECK 與觸發器強制，模型擅自標記的旗標於 `tidy` 清除。
  - **提示詞**：統一由 `verification/templates/*.j2` 透過 `prompts.py` 渲染，領域約定集中於 `conventions.j2`。
- **重抽**：待重抽單元由視圖 `annotation.reextraction_due` 依資料推得，輪數上限由 `extraction_attempt.round` 的 CHECK 強制，中斷重跑可自動收斂。
- **管線執行**：每個陳述獨立 commit，以 PostgreSQL 諮詢鎖保證同時只跑一個。保留樣本評估只計第一次看到的法規，不報準確度。

### 2. 驗證與接受

- **驗證紀錄與接受**：每個確定性閘門置於 `verification/gates/`，模型閘門置於 `model_gates/`，結果寫入 `annotation.verification`。`annotation.settle` 於必要方法皆通過（至少一項 `pass` 且無 `fail`）時升為 `confirmed`，有 `fail` 則 `rejected`，狀態變更一律記一列 `policy`。
- **獨立性**：抽取者不得驗證自己主張（觸發器強制）；模型閘門預設不得與抽取者同家族，合格者不足或逾時／無答案記為 `inconclusive`，不中斷管線。確定性閘門不證明語意忠實，預設不單獨接受陳述（`canon verify --require` 例外）。
- **模型配置與別名探測**：家族須於 `models.toml` 明宣告；金鑰名由端點名推導（`ENVCITE_<端點名大寫>_API_KEY`），依序自環境變數與系統鑰匙圈解析，禁止寫入設定檔或對話。管線與黃金測試集判定者啟動前以 `SameModel` 探測別名，答案逐字相同或回覆模型名不符即拒絕執行（`--no-distinct-check` 僅供除錯）。單元測試一律使用有名字與家族的替身，不呼叫真實模型。

### 3. 詞彙、授權與行政規則

- **詞彙與定義**：**不借用 SKOS 上下位**（含 `broader` 整份拒絕），「甲屬於乙」只經立法定義（`annotation.term_definition`）或授權命令成立，定義僅限同法內使用（不支援準用），且須兩個獨立方法皆通過才確認。`vocab.concept_match` 僅記等同對映。
- **載入規則（`annotate.load`）**：條文原文雜湊須存在於現行版本列（否則結束碼 `2`）；不確定概念標 `judgement: true`（限值不得標）；門檻來源四選一；責任基礎只屬主體、認定方式只屬限值；一個罰則陳述一個效果，所罰須為已確認 `prescriptive`（連不上存為 `held_penalty`）；「命令屆期未履行」前提須為 `order`。主張寫入即封存（`annotation.seal`），不可修改。

### 4. 推論

- **引擎（`reasoning/`）**：通用 `rules.lp`（clingo）**不含任何法條**、不用模型；案件事實由呼叫端提供，結論皆為**候選**，缺漏事實、需判斷要件、矛盾事實、未決衝突與罰則競合分開回報。**修改 `rules.lp` 必須執行 `tests/test_reasoning*.py`**。
- **法理與數值邊界**：
  - `order`（預防性處分）與 `disgorgement`（不法利得追繳）非行政罰，不適用行政罰法總則。
  - `sanction_trigger`（以另一效果為前提）隨前提成立或附條件；違憲失效版本於 SQL 排除並另列 `invalidated`；授權鏈斷裂標為授權依據待查並附條件。
  - 行政罰法第 18、26 條只給候選與額度；刑事優先只擋罰鍰與已宣告沒收之沒入，未給定則不套用。
  - 金額不進 clingo（受限 32 位元整數），比較與加總於 `facts.amounts` 處理。
- **對外語意**：未給許可量值為未知、認定方式不符不逕判、`evaluate_across` 不選最有利、`uncovered_laws` 等契約語意以 [`proto/README.md`](../../proto/README.md)〈標註與推論〉為準。

## Contract

- **介面契約**：[`canon.proto`](../../proto/envcite/canon/v1/canon.proto) 與 [`reasoning.proto`](../../proto/envcite/canon/v1/reasoning.proto)，錯誤原因清單見 [`proto/README.md`](../../proto/README.md)。法規自然鍵三欄必填，缺一即回 `INVALID_ARGUMENT`。
- **唯讀與強型別**：`ReasoningService` 唯讀；列舉不用字串、數值用 `Decimal`（十進位文字）、`AdministrativePenaltyFacts` 用 `optional`、日期用 ISO 8601 字串。`x-tenant-id`／`x-request-id` 只用於日誌與稽核。
- **錯誤處理**：統一丟出 `errors.ServiceError`，於 servicer 邊界由 `errors.maps_errors` 轉為 `google.rpc.Status`；呼叫端依 `reason` 分流，新增錯誤只增常數不改既有語意。
- **執行緒池與期限控制**：固定執行緒池搭配併發名額（`QUERY_EMBED_SLOTS`、`RERANK_SLOTS`、`Evaluate` 獨立名額）。請求剩餘期限同時作為連線池等待上限與 `statement_timeout`：等不到連線回 `BUSY`，查詢取消回 `READ_TIMEOUT`（`Evaluate` 回 `EVALUATION_TIMEOUT`）。gRPC 無期限時須經 `envcite_grpc.remaining_seconds` 轉換。連線 keepalive／ping 選項見 `envcite-grpc` 的 `connection.py`。

## Safety

- **資料出處標示**：全國法規資料庫、行政院公報（政府資料開放授權條款－第 1 版）、環境部主管法規查詢系統與函釋（CC BY 4.0）、立法院法律系統（須標示出處；該站僅支援舊式 TLS 重新協商）之來源皆須保留出處標示（快照 `license_notice`）。
- **禮貌取得**：立法院與環境部函釋網站無 API／robots.txt 限制或僅提供 HTML，取得維持**循序與禮貌間隔，不並行**。

## Done when

- `ruff check .`、`mypy src tests`、`pytest` 通過。
