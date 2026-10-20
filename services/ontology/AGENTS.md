# AGENTS.md

倉庫層規範（Layering、Contract、Safety、buf）見根 `AGENTS.md`；本檔只管 `services/ontology/`。

## 索引

| 文件 | 內容 |
|------|------|
| [`../../README.md`](../../README.md) | 倉庫總覽與目錄 |
| [`../../AGENTS.md`](../../AGENTS.md) | 倉庫層規範 |
| [`README.md`](README.md) | 本服務：介面、資料流、入口、標註管線、開發指令 |
| [`../../proto/README.md`](../../proto/README.md) | 契約、錯誤原因與推論的呼叫端用法 |
| [`../platform/`](../platform/README.md)、[`../consult/`](../consult/README.md) | 呼叫端，各有 README 與 `AGENTS.md` |

## Project

- **Name:** envcite-ontology（Python 套件 `envcite_ontology`）
- **Role:** 本體服務（shared, non-tenant data）：各來源的知識集中在本服務，一種來源一個本體，各有自己的 schema 與引用代號字母（法規本體 `law` 為 `L`）。新增來源是新增一個本體，不另立服務。由同倉的 `services/platform` 與 `services/consult` 呼叫。
- **Layout:** 共用機制（取得引擎、投影、驗證管線、gRPC 伺服器）在套件頂層；本體專屬的程式（來源、解析、規則）放進以本體 id 命名的子套件，資料表歸該本體自己的 schema。新增第二個本體時，把法規專屬的程式移進 `law/`，介面不變。拆成獨立服務只在：該本體需要獨立擴縮或發版、資料不能與共享資料同庫、或需要不同的執行環境。
- **Stack:** Python · uv · gRPC（grpcio，同步伺服器）· PostgreSQL（psycopg 3）· ruff · mypy (strict) · pytest；版本以根 `.python-version`、`pyproject.toml`、`uv.lock` 為準。
- **Data model:** **關聯式實表，PostgreSQL 是唯一真值**。以法規本體為例，schema 依職責分：`corpus`（語料）、`retrieval`（檢索投影）、`vocab`（受控詞彙）、`annotation`（規範陳述）。法規以（識別碼方案，在地識別碼）為鍵；效力逐條文單元記載，查詢逐單元解析（`corpus.provisions_at`）；標題掛在 `legal_expression`（法規名稱可因修正而變，屬公布版本）；標註錨定（單元，內容雜湊），不錨定版本列。表結構以 `migrations/*.sql` 為準。

## Commands

只用 `uv`（不用 pip／venv／poetry）。從**本目錄**執行。

```bash
uv sync --all-packages       # 整個 workspace（.venv 與 uv.lock 在倉庫根）
uv add --raw <pkg>           # 執行期依賴，只寫名稱——版本在 uv.lock
uv run python -m pytest       # 測試
uv run ruff check .           # lint（--fix 可自動修）
uv run ruff format .          # 格式化
uv run python -m mypy src tests   # 型別檢查（strict）
../../libs/python/envcite-proto/scripts/generate.sh   # 改了 proto 之後重新產生 gRPC 程式碼
ONTOLOGY_TEST_DSN=postgresql://... uv run python -m pytest   # 用現成的資料庫測試
ONTOLOGY_DSN=postgresql://... uv run envcite-ontology       # 起伺服器
ONTOLOGY_DSN=postgresql://... ACQUIRE_SNAPSHOT_DIR=<目錄> uv run envcite-sync moj   # 排程週期：取得 → 匯入 → 投影
docker build -f Dockerfile ../..   # 映像（context = 倉庫根）；CI 在 main 推送 :<sha>
```

- Proto lint／`buf breaking` 從倉庫根執行，見根 `AGENTS.md`。全部入口見 `pyproject.toml` 的 `[project.scripts]` 與 `README.md`。
- 工具設定（ruff、mypy、pytest）在倉庫根 `pyproject.toml`。程式碼在 `src/envcite_ontology/`，測試在 `tests/`；gRPC 產生碼從 `envcite-proto` 匯入。
- **環境變數以 `src/envcite_ontology/settings.py` 為唯一來源**（名稱、必填、預設值）；入口不直接讀 `os.environ`；加變數時同步 `.env.example`（`tests/test_settings.py` 守門）。
- 服務間認證由叢集的服務網格（mTLS 加授權政策）負責，服務本身不驗證呼叫端。
- 測試需要**真實的 PostgreSQL**：設 `ONTOLOGY_TEST_DSN`，或留空讓 testcontainers 起一個（需要 Docker daemon）。不用替身，因為本服務一半的讀取邏輯在 SQL 裡（逐單元解析、時態鍵、整數陣列排序），對替身測試等於不測那一半。
- **綱要由 `migrations/*.sql` 描述，依檔名順序套用。已套用的檔案不可修改**——要改就新增一個檔，這條由 `schema_migration` 的雜湊比對強制，不靠記得。

## 語料管線

- **取得**（`envcite-acquire <來源>`）：引擎負責下載、SHA-256、冪等判斷、以雜湊為名存檔與寫入 `source.*`；來源只實作 `Source`，在 `cli.SOURCES` 登記。
- **匯入**（`envcite-import <來源> [--dry-run]`）：每個來源取最新快照；`law.parse` 是純函式；整批比例過 `audit.LIMITS` 才寫入，否則結束碼 2；單一法規的問題記入 `source.import_problem`，error 使該部不寫入。**改變解析輸出的修改必須遞增 `law.PARSER_VERSION`**，已匯入的法規才會以新版重寫。已匯入法規的內容改變是增量（`ingest/write.py` 的 `apply`）：新增版本、更正、更新推導，或拒絕（`version_regressed`）；更正以新增列取代舊列（`superseded_at`），查詢只看現行列。
- **檢索投影**：匯入成功後 `projection.rebuild` 補齊索引——分詞器識別碼（含標題詞典雜湊）不符的列與被取代版本的列先刪，再補缺的條。索引以段落文字的雜湊為鍵、不指向版本列，所以更正後無從陳舊。伺服器的詞典啟動時讀一次，**重建之後要重啟服務**。
- **週期**（`envcite-sync <來源>`）：取得、匯入、投影依序執行，每步冪等。結束碼：`0` 成功（含無事可做）；`1` 暫時性（連線、資料庫、下載內容不對）；`2` 重試無益（資料形狀不合、快照不存在、整批比例越線）。
- 分詞由本服務以 `envcite-segmenter` 做；**索引端與查詢端必須用同一個分詞器**。

## 標註、驗證與推論

- 標註由抽取器（語言模型）寫成 JSON，經 `envcite-annotate <檔>` 載入，或由 `envcite-pipeline` 整條線完成。**沒有人審核標註**：陳述以 `extracted` 進入，經驗證閘門後依政策升為 `confirmed`，**只有已確認（或規則推導）的陳述參與推論**。
- **詞彙與用語定義**：受控詞彙只有方案、概念的代碼與名稱、別名，**沒有上下位**（不借用 SKOS：本系統不以 RDF 發佈、不與外部詞表互通；0022 移除了只寫不讀的 `broader_id`，載入帶 `broader` 的概念整份拒絕）。「甲屬於乙」的法律依據是立法定義或授權命令，不是詞彙的階層。立法定義用 `annotation.term_definition`（錨定單元與內容雜湊，與陳述同一組確認狀態；`annotation.definitions_at` 逐日解析），其詞彙方案以文件的 `scheme.law` 限定在一部法規；限值概念留在跨法共用方案，授權命令與被授權的法律才解析得到同一個概念（`threshold_at` 以概念 id 比對）。推論讀已確認的用語定義：要件以 `value_concept` 與概念值比較（只有 `eq`／`ne`），案件事實用 `FactValue.concept`；定義只在要件所屬的法規內使用（準用尚未實作），成員關係可遞移（定義的成員本身又被定義）。可執行閘門以「概念本身／不屬於任何用語的值」翻轉概念值要件，不靠定義。管線先抽定義（`verification/definitions.py`：只處理「本法用語，定義如下」之下的款，載入該法自己的方案 `def.<pcode>`，狀態 extracted），再抽陳述；定義的確認要兩個互相獨立的方法都通過（`verification/definition_gates.py`：`literal` 引文、用語與成員逐字在原文，加另一家族模型只看原文的往返重讀，成員漏列或多列都算不同），任一失敗就駁回，沒有重讀者就不確認任何定義；陳述抽取讀本法用語清單，要件以 `value_concept` 指名其中之一，代碼從源頭一致。定義項不當規範抽取。
- 載入規則：每個陳述附它所讀的條文原文，載入時算雜湊並要求該單元現行的某個版本列確有這個雜湊，否則整份拒絕（結束碼 2）；狀態取文件的 `review`，預設 `extracted`，載入不接受任何陳述；不確定法律概念或裁量的要件標 `judgement: true`（系統不從事實判定它，限值不得標）；一個罰則陳述一個效果（改善命令等非處罰的行政命令用效果 `order`，按次處罰用 `repeating: true`）；一次載入是一個交易，不冪等（要修正，把舊陳述改為 `rejected` 再載入）。
- **驗證**（`verification/`）：確定性閘門一個閘門一個模組，在 `verification/gates/`；模型閘門在 `model_gates.py`。每次結果寫一列 `annotation.verification`；`annotation.settle(必要方法)` 在必要方法最近一次都 pass 或 not_applicable、至少一個 pass、且沒有任何 fail 時升為 `confirmed`，有 fail 則 `rejected`，每次升降也記一列。
  - **抽取者不得驗證自己的陳述**（資料庫強制）；驗證者**預設也不得與抽取者同一家族**，合格者不足則 `inconclusive`。
  - **確定性閘門不證明語意忠於原文**。`envcite-verify`（只跑確定性閘門）用預設政策不接受任何陳述；只用確定性閘門接受要明說 `--require`。
  - 閘門失敗、逾時或模型無答案是 `inconclusive`，不是駁回、也不中斷。
- **模型配置只有設定檔一條路**（`models.toml`，範本 `models.example.toml`，`--models` 必填）：角色、端點與**明宣告的家族**都寫在裡面。**金鑰不入設定檔**，只寫它在哪裡（環境變數名稱，或專案內被 `.gitignore` 忽略的檔案路徑）；在 git 工作樹裡又沒被忽略的金鑰檔拒絕載入。本機的 `models.toml` 已列入 `.gitignore`。
  - **託管服務的模型名稱不保證是它所指的模型**，名字不同不等於家族不同：管線預設先對所有模型問固定的開放式問題，答案逐字相同就拒絕開跑（`SameModel`）；`--no-distinct-check` 略過。
  - 單元測試全用有名字與家族的替身，**沒有呼叫過任何模型**。
- 抽取者輸出的形狀不可信：槽位直接寫成字串就是它的字面（`tidy`）；形狀問題請模型修有限輪（`shape_issues`），修不好只丟那個陳述，不連累同單元其餘的陳述。罰則處罰的是它引用的別條，逐單元抽取連不上，由所有單元抽完後的 `link.py` 連結；連好的罰則仍要過引用閘門與評審團。
- 本體的約定（只有規範性陳述有義務方向、授權不是許可、罰則一個效果一個陳述、按次處罰是另一個帶「屆期仍未改善」要件的陳述）寫在 `conventions.py` 一處，抽取者、往返重抽者與評審團的提示都由它組成，不各寫各的。
- **管線**可重跑、每個陳述 commit；`--pending` 的待辦由資料推得（`annotation.extraction_attempt` 記下模型答過的單元，包括沒有產出陳述者，所以重跑收斂）；同一時間只跑一個（PostgreSQL 諮詢鎖）。保留樣本評估看過的法就用掉了，只報第一次看的計數，不報準確度。
- **推論**（`reasoning/`，`evaluate(conn, as_of, case)`）：通用推論程式 `rules.lp`（clingo）**不含任何法條**，也不用模型；`facts.py` 把某日有效、已確認的陳述解析後寫成事實，案件事實由呼叫端給。結論都是**候選**（違反、合規、附條件、被排除、不適用、無法判定）；缺的事實、需判斷的要件、矛盾的事實、未決的衝突與罰則競合分開回報。
  - `order`（預防性處分）不是行政罰，行政罰法總則的排除、減輕、時效與一行為都不適用；罰則的 `discretionary`（效果裁量）與要件的 `judgement`（構成要件的不確定法律概念）是兩個層次。
  - 行為後法律有變更時，`evaluate_across` 依行政罰法第 5 條列出裁處時之法律與期間內每一組不同的陳述，各自推論，**不替呼叫端選「最有利」者**。
  - 語料只有現行全文：行為日早於語料持有某法規的第一個版本時，該組帶 `uncovered_laws`，空的結果不代表當時沒有規定。
  - **改 `rules.lp` 要跑 `tests/test_reasoning*.py`**（真實 schema、不用替身）。

## 可觀測性與連線

- OpenTelemetry，與 platform 同一個約定：只認標準 `OTEL_*` 鍵，**沒設 `OTEL_EXPORTER_OTLP_ENDPOINT` 就什麼都不做**（`libs/python/envcite-grpc` 的 `telemetry.py`）。設了之後匯出 gRPC 追蹤（接續 platform 帶來的 traceparent）與指標；標籤只用有限集合，租戶與案件內容不進指標。每個一元呼叫的 RED 指標（rate、errors、duration）由 `envcite-grpc` 的 `metrics.py` 的攔截器提供，隨 telemetry 啟用，服務本身不必另接；名稱與標籤見 `proto/README.md`。
- 連線：伺服器與客戶端選項成套放在 `libs/python/envcite-grpc` 的 `connection.py`。**伺服器的最短 ping 間隔必須小於客戶端的 keepalive 間隔**，改一邊要看另一邊（測試守著這個不等式）；platform 的對應設定在 `InternalGrpcClient`。

## Contract

- 本服務契約是倉庫根的 `proto/envcite/ontology/v1/ontology.proto` 與 `reasoning.proto`（單一真值與 breaking 規則見根 `AGENTS.md`）。
- 資料不分租戶；`x-tenant-id`／`x-request-id` metadata 只用於日誌與稽核。
- `ReasoningService` 唯讀。訊息強型別：列舉不用字串、要件用 `Requirement → Disjunct → Condition` 表示析取範式、案件事實的值用 `oneof`、數值用 `Decimal`（十進位文字，不用浮點）、行政罰法總則的事實用 `AdministrativePenaltyFacts`（`optional`：缺席是未知，不是否）。日期維持 ISO 8601 字串。
- 錯誤一律 `errors.ServiceError`，由 `errors.maps_errors` 在 servicer 邊界轉成 `google.rpc.Status`：標準狀態碼加一個 `ErrorInfo`（`reason` 為 UPPER_SNAKE_CASE，`domain` 為 `ontology.envcite`），引數錯誤再加 `BadRequest`（全部欄位一次回報）。呼叫端依 `reason` 分流，不解析訊息；reason 清單在 `proto/README.md`。新增錯誤是新增常數，不改現有 reason 的意思。
- 同步伺服器的執行緒池是固定的：`Evaluate` 有自己的並行名額（等不到名額，在呼叫的期限內以 `RESOURCE_EXHAUSTED`／`BUSY` 失敗），期限同時成為資料庫的 `statement_timeout`；伺服器另設 `maximum_concurrent_rpcs`。gRPC 在呼叫沒有期限時 `time_remaining()` 回極大的數，不是 `None`，用 `_budget` 轉換。

## Safety

- 不手改產生物：倉庫根 `uv.lock`（用 `uv add/remove/lock`）、`.venv/`、`libs/python/envcite-proto/src/**/*_pb2*`（跑其 `scripts/generate.sh`）。
- 全國法規資料庫的資料必須標示出處（政府資料開放授權條款－第 1 版）。測試資料（`tests/sample.py`）節錄自它，並經真實的匯入程式匯入。
- 金鑰不進設定檔、不貼進對話、不提交。

## Done when

- ruff、mypy、pytest 通過；改了 proto 則兩側產生碼同一個 commit 更新，`proto/README.md` 同步。
