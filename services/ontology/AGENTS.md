# AGENTS.md

Vendor-neutral instructions for coding agents. 倉庫層規範（Layering、Contract、Safety、buf）見根 `AGENTS.md`；本檔只管 `services/ontology/`。

## Project

- **Name:** env-law-ontology (Python package `env_law_ontology`)
- **Role:** 環境法規本體論服務（shared, non-tenant data）；由同倉的 `services/platform`與 `services/consult` 呼叫
- **Stack:** Python 3.14 · uv · gRPC（grpcio，同步伺服器）· PostgreSQL 18（psycopg 3）· ruff · mypy (strict) · pytest
- **Data model:** **關聯式實表**（PostgreSQL 是唯一真值），schema `corpus`（`legal_resource`、`legal_expression`、`provision`／`provision_version`、`lifecycle_event`）、`retrieval`（`lexical_index`：內容定址、逐段落）、`vocab`（受控詞彙）與 `annotation`（規範陳述、要件、但書、優先、罰則；錨定（單元，內容雜湊），不錨定版本列）。法規以（識別碼方案，在地識別碼）為鍵，全國法規資料庫為 pcode；效力逐條文單元記載，查詢逐單元解析（`corpus.provisions_at`）。標題掛在 `legal_expression`：法規名稱可因修正而變，屬公布版本。綱要設計見 `env-law-docs` 的 02-04；其餘表隨其管線加入。論據見 `env-law-docs` 的 01-03。
- **Status:** gRPC `OntologyService`（GetLaw、ListArticles、SearchArticles）讀 PostgreSQL；SearchArticles 為字面檢索（分詞由本服務以 `env-law-segmenter` 做，詞典於啟動時自資料庫讀取）；資料庫預設是空的；全國法規資料庫的現行版本由 `env-law-sync moj`（或分步的 `env-law-acquire`、`env-law-import`）寫入，並補齊檢索投影 `retrieval.lexical_index`（以段落文字的雜湊為鍵，不指向版本列，所以更正後無從陳舊；缺列由 `retrieval.missing_lexical` 列出）；規範標註由抽取器（語言模型）寫成 JSON，經 `env-law-annotate <檔>` 載入（`src/env_law_ontology/annotate.py`，格式見其模組說明），推論只取已確認或規則推導的陳述（`annotation.statements_at`），委任要件的門檻值取授權命令當日有效的限值（`annotation.threshold_at`）；標註與推論由 gRPC `ReasoningService`（ListStatements、Evaluate）唯讀對外提供，見 Contract

## Commands

Use `uv` only (no pip/venv/poetry). Run from **this directory** (`services/ontology/`).

```bash
uv sync --all-packages       # install the whole workspace (.venv and uv.lock live at the repo root)
uv add --raw <pkg>           # runtime dep, name only — the version lives in uv.lock
uv run python -m pytest       # test
uv run ruff check .           # lint (add --fix to autofix)
uv run ruff format .          # format
uv run python -m mypy src tests   # typecheck (strict)
../../libs/python/env-law-proto/scripts/generate.sh   # regenerate gRPC code after editing protos
ONTOLOGY_TEST_DSN=postgresql://... uv run python -m pytest   # use an existing database
ONTOLOGY_DSN=postgresql://... uv run env-law-ontology   # run server (:50051)
ONTOLOGY_DSN=postgresql://... ACQUIRE_SNAPSHOT_DIR=/tmp/snapshots uv run env-law-acquire moj   # acquire one source (CronJob entrypoint)
ONTOLOGY_DSN=postgresql://... ACQUIRE_SNAPSHOT_DIR=/tmp/snapshots uv run env-law-import moj [--dry-run]   # snapshot → corpus; --dry-run parses and gates only
ONTOLOGY_DSN=postgresql://... ACQUIRE_SNAPSHOT_DIR=/tmp/snapshots uv run env-law-sync moj   # the scheduled cycle: acquire → import → projection (CronJob entrypoint)
docker build -f Dockerfile ../..   # image (context = repo root; CI job `ontology-image` pushes :<sha> on main)
```

- Proto lint／`buf breaking` 從倉庫根執行，見根 `AGENTS.md`。
- Tool config (ruff, mypy, pytest) lives in the repo-root `pyproject.toml`. Code in `src/env_law_ontology/`, tests in `tests/`; generated gRPC code is imported from `env-law-proto` (`env_law.ontology.v1`).
- 環境變數以 `src/env_law_ontology/settings.py` 為唯一來源（名稱、必填、預設值）；入口不直接讀 `os.environ`，加變數時同步 `.env.example`（`tests/test_settings.py` 守門）。服務間認證由叢集的服務網格（mTLS 加授權政策）負責，服務本身不驗證呼叫端。
- 取得（`env-law-acquire <來源>`，`src/env_law_ontology/acquire/`）：引擎負責下載、SHA-256、冪等判斷、以雜湊為名存檔與寫入 `source.*`；來源只實作 `Source`（`feeds`、`tls()`、`read()`），在 `cli.SOURCES` 登記。環境變數 `ONTOLOGY_DSN`（可寫端點）、`ACQUIRE_SNAPSHOT_DIR`。結束碼 0 成功或未變、1 暫時性失敗（可重試）、2 資料不合預期（重試無益）。
- 匯入（`env-law-import <來源> [--dry-run]`，`src/env_law_ontology/ingest/`）：每個來源取最新快照，`law.parse` 為純函式（條文切分 `articles`、沿革 `history`、生效日 `commencement`）；整批比例過 `audit.LIMITS` 才寫入，否則結束碼 2。單一法規的問題記入 `source.import_problem`，error 使該部不寫入。**改變解析輸出的修改必須遞增 `law.PARSER_VERSION`**，已匯入的法規才會以新版重寫；已匯入的法規內容改變是增量（`ingest/write.py` 的 `apply`）：新增版本、更正、更新推導、拒絕（`version_regressed`）；更正與推導修正以新增列取代舊列（`superseded_at`），查詢只看現行列；下層單元的文字改變時，所屬條的列也換新（原為舊索引以條的列為鍵而設；索引現已內容定址，不再依賴，行為暫時保留）。匯入成功後 `projection.rebuild` 補齊索引：分詞器識別碼（含標題詞典雜湊）不符的列與被取代版本的列先刪，再補缺的條；服務端的詞典啟動時讀一次，重建之後要重啟服務。
- 標註（`env-law-annotate <檔>`，環境變數 `ONTOLOGY_DSN`）：每個陳述附它所讀的條文原文，載入時算雜湊並要求該單元現行的某個版本列確有這個雜湊，否則整份拒絕（結束碼 2）；狀態取文件的 `review`，預設 `extracted`，載入不接受任何陳述；不確定法律概念或裁量的要件標 `judgement: true`（系統不從事實判定它，限值不得標）；一個罰則陳述一個效果：改善命令等非處罰的行政命令用效果 `order`，按次處罰用 `repeating: true`；一次載入是一個交易，不冪等（要修正，把舊陳述改為 `rejected` 再載入）。
- 推論（`src/env_law_ontology/reasoning/`，`evaluate(conn, as_of, case)`）：通用推論程式 `rules.lp`（clingo）不含任何法條；`facts.py` 把某日有效、已確認（或規則推導）的陳述經 `annotation.statement_versions_at` 與 `annotation.threshold_source_at` 解析後寫成 ASP 事實，案件事實由呼叫端給。結論都是候選：違反、合規、附條件、被排除、不適用、無法判定；罰則只列候選，罰則自己的要件也涵攝；缺的事實、需判斷的要件、矛盾的事實、未決的衝突與罰則競合分開回報。`order`（改善命令等預防性處分）不是行政罰，行政罰法總則的排除、減輕、時效與一行為都不適用；罰則的 `discretionary` 是效果裁量（條文寫「得」），與要件的 `judgement`（構成要件的不確定法律概念）是兩個層次。行為後法律有變更時，`reasoning.evaluate_across(conn, 行為日, 裁處日, 案件)` 依行政罰法第 5 條列出裁處時之法律與行為日到裁處日之間每一組不同的陳述，各自推論，不替呼叫端選「最有利」者。語料只有現行全文，行為日早於語料持有某法規的第一個版本時，該組帶 `uncovered_laws`（gRPC：`Outcome.uncovered_law_ids`），空的結果不代表當時沒有規定。**改 `rules.lp` 要跑 `tests/test_reasoning*.py`（真實 schema、不用替身）。** 經 `ReasoningService.Evaluate` 對外（唯讀、可重試、有自己的並行名額）；沒有人審過的標註不產生任何結論。
- 可觀測性：OpenTelemetry，與 platform 同一個約定——只認標準 `OTEL_*` 鍵，**沒設 `OTEL_EXPORTER_OTLP_ENDPOINT` 就什麼都不做**（`libs/python/env-law-grpc` 的 `telemetry.py`）。設了之後匯出 gRPC 追蹤（接續 platform 帶來的 traceparent）與指標：`env_law.reasoning.evaluate.duration`（依 outcome：ok、invalid、busy、deadline、error；busy 比率是擴容訊號）、`env_law.reasoning.evaluate.in_flight`，以及讀自 0020 健康檢視的 `env_law.annotation.statements`（各狀態的陳述數；`confirmed` 為 0 表示推論沒有可用的規範）與 `env_law.verification.gate_results`（各閘門最近一次的結果分布，閘門通過率是核心健康指標）。標籤只用有限集合，租戶與案件內容不進指標。兩個檢視也可直接 `psql` 查。
- 連線：伺服器選項與客戶端選項成套放在 `libs/python/env-law-grpc` 的 `connection.py`（最長壽命 5 分鐘加 30 秒寬限、keepalive、只對 `UNAVAILABLE` 的重試）；伺服器的最短 ping 間隔必須小於客戶端的 keepalive 間隔，改一邊要看另一邊（測試守著這個不等式）。平台的對應設定在 `InternalGrpcClient`。論據見 `env-law-docs` 的 02-06 §6。
- 管線（`env-law-pipeline`）：抽取 → 驗證 → 接受，可重跑、每個陳述 commit。`--law <pcode>` 指定法規，或 `--pending [--limit N]` 自己找「現行文字還沒被此抽取者與版本答過」的法規（待辦由資料推得，`annotation.extraction_attempt` 記下模型答過的單元，包括沒有產出陳述者，所以重跑收斂）；同一時間只跑一個（PostgreSQL 諮詢鎖），要排程就以同一個映像跑成 Job。
- 驗證（`src/env_law_ontology/verification/`，`env-law-verify --require <方法,…>`）：**沒有人審核標註**。抽取器（`extract.py`，模型是一個 `Model` 介面）寫入的陳述是 `extracted`，由閘門驗證、依政策接受：閘門是 `Gate`（確定性閘門在 `verification/gates/`，一個閘門一個模組；`numbers`：額度與帶單位的門檻值對原文，且比較符號含不含本數要與原文的「以上、以下、以內、未滿、未達」一致（「逾」「超過」沒有查到明文規則，不判定）；`references`：罰則所處罰的陳述是原文引用的那一條，且那個陳述已被接受（還在 `extracted` 則等下一輪，被駁回則失敗）；`executable`：推論程式可評估且結論如要件所預期地翻轉；`provisos`：原文有但書，同一條裡要有承接它的例外陳述，否則停在 `inconclusive`——往返驗證的重抽者沒看過原文，漏掉的但書會在描述裡一起消失，只有這個閘門讀原文；效果面的但書目前表達不了，也停在這裡），每次結果寫一列 `annotation.verification`；`annotation.settle(必要方法)` 在必要方法最近一次都 pass 或 not_applicable、至少一個 pass、且沒有任何 fail 時升為 `confirmed`，有 fail 則 `rejected`，每次升降也記一列。抽取者不得驗證自己的陳述（資料庫強制）。**確定性閘門不證明語意忠於原文**；模型閘門在 `model_gates.py`：`roundtrip`（標註 → 確定性描述 → 另一家族的模型重抽 → 比對結構）、`independent_test`（另一個模型只讀原文產生案例，丟進推論程式比對）、`panel`（不同家族的評審團取三分之二多數）；驗證者不得是抽取者，**預設也不得與抽取者同一家族**，合格者不足則 `inconclusive`，所以 `env-law-verify`（只跑確定性閘門）用預設政策不接受任何陳述，只用確定性閘門接受要明說 `--require numbers,references,executable`。`calibration.ppi_mean` 以外部既有的標準答案估計準確度，不另請人標。整條線（抽取 → 驗證 → 接受）是 `env-law-pipeline --law <pcode> --models models.toml`：模型配置在一個 TOML 檔（範本 `models.example.toml`，`config.py` 載入）——角色（抽取者、往返驗證的重抽者、獨立測試的設計者、評審團）、端點與**明宣告的家族**都寫在裡面，金鑰不入設定檔，只寫它在哪裡：環境變數名稱（`api_key_env`），或專案內不進版控的檔案路徑（`api_key_file`，相對於設定檔，放在被 `.gitignore` 忽略的 `services/ontology/.secrets/`；在 git 工作樹裡又沒被忽略的金鑰檔拒絕載入）；兩個端點可以同時用；本機的 `models.toml` 已列入 `.gitignore`。模型配置只有設定檔一條路，`--models` 必填（`models.py`：`Model` 介面有名字與家族；兩個轉接器：`provider = "anthropic"`（需要 `uv sync --extra llm` 與 `ANTHROPIC_API_KEY`），與預設的 `chat`（OpenAI 相容的 Chat Completions，只用標準函式庫；端點、金鑰、家族都由設定檔給；回覆中的 ```json 圍欄會被剝掉））。**託管服務的模型名稱不保證是它所指的模型**（同一個模型常有幾個別名，例如 `gpt-oss-120b` 與 `mistral-small-4` 溫度 0 下逐字相同），名字不同不等於家族不同：`env-law-pipeline` 預設先對所有模型問兩個固定的開放式問題，答案逐字相同就拒絕開跑（`SameModel`），`--no-distinct-check` 略過；這只抓得到完全相同的別名。罰則條文處罰的是它引用的別條，逐單元抽取連不上：抽取者省略 `sanction.punishes`（單元的原文引用了別條、而沒有引用自己時，即使抽取者把罰則連到同單元重述義務的陳述，也一律當作省略），所有單元抽完後 `link.py` 把罰則連到被引用條文（`citations`）裡的陳述，只有一個候選就直接連、不呼叫模型，幾個才請抽取模型挑一個，找不到候選的丟掉並記下（標註文件的 `sanction.punishes_id` 可直接指向資料庫裡已有的陳述）；連好的罰則仍要過引用閘門與評審團。描述給模型閘門看時，成立與否的要件寫「成立」而不是「= 1」。款、目只是半句話（「有下列情形之一者，處…罰鍰：一、…」，罰鍰在項、違反的行為在款）：抽取者與評審團另外看得到上層條文（`context.py`，只供理解；引文與錨點仍在單元自己的文字上），數字閘門的額度對上層與單元合起來的文字，且下限與上限要是原文同一組（一般與工商廠、場常分級）。往返驗證的重抽者在非規範性陳述上寫的義務方向不算差異，與載入時丟掉多餘方向一致。抽取者輸出的形狀不可信：槽位直接寫成字串就是它的字面（`tidy`）；要件沒有 concept 或用了沒在 concepts 宣告的代碼（載入器會整份拒絕），與完整性問題一起請模型修（最多兩輪，`shape_issues`），修不好只丟那個陳述，不連累同單元其餘的陳述。本體的約定（只有規範性陳述有義務方向、授權不是許可、罰則一個效果一個陳述、按次處罰是另一個帶「屆期仍未改善」要件的陳述）寫在 `conventions.py` 一處，抽取者、往返重抽者與評審團三個提示都由它組成，不各寫各的：兩次真實跑各抓到一個「約定只告訴了一端」的缺陷。沒給過管線看的法規上的保留樣本評估：`uv run python tools/holdout.py <pcode> <容器名> <埠> --log <檔>`（取樣規則、拋棄式資料庫、匯入、跑管線都在裡面；金鑰只從 `models.toml` 指名的環境變數讀，不讀也不寫金鑰檔；匯入程式拒絕的節錄在呼叫任何模型之前就停）。看過的法就用掉了，只報第一次看的計數，不報準確度。驗證每個陳述的閘門跑完就 commit（模型呼叫最貴，崩潰不丟已問到的答案，長跑也看得見進度；每十個陳述記一行 `驗證 i/n`）。`chat` 轉接器對暫時性的失敗（逾時、斷線、408／409／425／429／5xx）以指數退避重試（預設三次，設定檔的 `max_retries` 可改；伺服器給的 `Retry-After` 照辦、上限一分鐘），其餘 4xx 直接報；重試用盡丟 `ModelError`，抽取把該單元記為沒有答案（不寫入，下次跑會再試），閘門記為 `inconclusive`（不駁回、不中斷）。測試全用有名字與家族的替身，單元測試沒有呼叫過任何模型。
- 週期（`env-law-sync <來源>`，`src/env_law_ontology/sync.py`）：取得、匯入、檢索投影依序執行，每步冪等，每個週期都全跑。結束碼：0 成功（含無事可做）；1 暫時性（連線、資料庫、下載到的不是 zip）；2 重試無益（`InvalidSnapshot`：資料形狀不合、快照檔不存在；`BatchRejected`：整批比例越線）。只為本機開發：沒有寫入者互斥、執行紀錄與觀測信號，投影重建期間與識別碼改變後、重啟服務前，字面檢索查不到東西。設計見 env-law-docs 03-04。
- 測試需要**真實的 PostgreSQL**：設 `ONTOLOGY_TEST_DSN` 指向現成的資料庫，或留空讓 testcontainers 起一個（需要 Docker daemon）。不用替身，因為本服務一半的讀取邏輯在 SQL 裡（逐單元解析、時態鍵、整數陣列排序），對替身測試等於不測那一半。
- 綱要由 `migrations/*.sql` 描述，依檔名順序套用。**已套用的檔案不可修改**——要改就新增一個檔，這條由 `schema_migration` 的雜湊比對強制，不靠記得。

## Contract

- 本服務契約是倉庫根的 `proto/env_law/ontology/v1/ontology.proto`（單一真值與 breaking 規則見根 `AGENTS.md`）。
- Data is not tenant-scoped; `x-tenant-id` / `x-request-id` metadata are for logging/audit only.
- `proto/env_law/ontology/v1/reasoning.proto` 是 `ReasoningService`：`ListStatements`（某日有效、已確認的陳述）與 `Evaluate`（行為日、裁處日加案件事實，回候選結論；法律在期間內有變更則一併列出每一組）。訊息強型別：列舉不用字串、要件用 `Requirement → Disjunct → Condition` 表示析取範式、案件事實的值用 `oneof`、數值用 `Decimal`（十進位文字，不用浮點）、行政罰法總則的事實用 `AdministrativePenaltyFacts`（`optional`：缺席是未知，不是否）。日期維持 ISO 8601 字串，與既有契約一致。
- 錯誤一律 `errors.ServiceError`，由 `errors.maps_errors` 在 servicer 邊界轉成 `google.rpc.Status`：標準狀態碼加一個 `ErrorInfo`（`reason` 為 UPPER_SNAKE_CASE，`domain` 為 `ontology.env.law`），引數錯誤再加 `BadRequest`（全部欄位一次回報）。呼叫端依 `reason` 分流，不解析訊息；reason 清單在 `proto/README.md`。新增錯誤是新增常數，不改現有 reason 的意思。
- 同步伺服器的執行緒池是固定的：`Evaluate` 用 `EVALUATE_SLOTS` 個名額（等不到名額，在呼叫的期限內以 `RESOURCE_EXHAUSTED`／`BUSY` 失敗），期限同時成為資料庫的 `statement_timeout`（`OntologyStore.read_connection`）；伺服器另設 `maximum_concurrent_rpcs`。gRPC 在呼叫沒有期限時 `time_remaining()` 回極大的數，不是 `None`，用 `_budget` 轉換。
- 反射（grpcurl 等）開著。

## Safety

- Do not hand-edit generated paths: the repo-root `uv.lock` (change via `uv add/remove/lock`), `.venv/`, `libs/python/env-law-proto/src/**/*_pb2*` (run its `scripts/generate.sh`).
- All data from 全國法規資料庫 must cite it (政府資料開放授權條款－第1版, attribution required). Test data (`tests/sample.py`) excerpts it (2026-09-18) and is imported through the real importer.

## Done when

- ruff, mypy, pytest and `buf lint` pass — enforced by `.gitlab-ci.yml` (`ontology` job; `python-proto` fails if generated gRPC code is stale; `proto` job runs `buf breaking` against the MR target branch).

