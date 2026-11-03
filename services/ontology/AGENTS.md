# AGENTS.md

倉庫層規範見根 `AGENTS.md`；本檔只管 `services/ontology/`。

## Project

- **Name:** envcite-ontology（Python 套件 `envcite_ontology`）
- **Role:** 本體服務（shared, non-tenant data）：各來源的知識集中在本服務，一種來源一個本體，各有自己的 schema 與引用代號字母（呼叫端看到的規則在倉庫根 `proto/README.md`）。新增來源是新增一個本體，不另立服務；拆成獨立服務只在該本體需要獨立擴縮或發版、資料不能與共享資料同庫、或需要不同的執行環境。由 `services/platform` 與 `services/consult` 呼叫。
- **Layout:** 共用機制（取得引擎、投影、驗證管線、gRPC 伺服器）在套件頂層；本體專屬的程式（來源、解析、規則）放進以本體 id 命名的子套件，資料表歸該本體自己的 schema。
- **Stack:** Python · uv · gRPC（grpcio，同步伺服器）· PostgreSQL（psycopg 3）· ruff · mypy (strict) · pytest
- **Data model:** **關聯式實表，PostgreSQL 是唯一真值**，表結構以 `migrations/*.sql` 為準。法規以（識別碼方案，在地識別碼）為鍵；效力逐條文單元記載、逐單元解析；法規名稱可因修正而變，標題屬公布版本；標註錨定（單元，內容雜湊），不錨定版本列。

## Commands

Python 一律經 `uv`。從**本目錄**執行。

```bash
uv sync --all-packages       # 整個 workspace（.venv 與 uv.lock 在倉庫根）；標註管線用 Anthropic 模型時加 --extra llm
uv run python -m pytest
uv run ruff check .          # --fix 可自動修
uv run ruff format .
uv run python -m mypy src tests
../../libs/python/envcite-proto/scripts/generate.sh   # 改了 proto 之後重新產生 gRPC 程式碼
docker build -f Dockerfile ../..   # 映像（context = 倉庫根）

# 本機跑：入口清單見 README；環境裡已有的同名變數優先於 .env
cp .env.example .env
uv run --env-file .env envcite-migrate
uv run --env-file .env envcite-sync moj
uv run --env-file .env envcite-ontology
```

- **環境變數以 `src/envcite_ontology/settings.py` 為唯一來源**；入口不直接讀 `os.environ`；加變數時同步 `.env.example`（`tests/test_settings.py` 守門）。
- 測試用**真實的 PostgreSQL**：設 `ENVCITE_ONTOLOGY_TEST_DSN`，或留空讓 testcontainers 起一個（需要 Docker daemon）。不用替身，因為本服務一半的讀取邏輯在 SQL 裡。
- **綱要由 `migrations/*.sql` 依檔名順序套用。已套用的檔案不可修改**——要改就新增一個檔，由 `schema_migration` 的雜湊比對強制。
- **資料庫角色**（定義在 `migrations/` 的角色遷移）：`reader`（伺服器）、`writer`（取得、匯入、投影、標註管線）、`reviewer`（writer 加手動審核與載入已審核的主張）。標註層對寫入者**只能新增**：主張寫入後內容不改不刪，驗證紀錄只經 `record_verification`、接受只經 `settle`、手動審核只經 `set_confirm_state`（都是 `SECURITY DEFINER`）。角色由部署庫建立，遷移只授權；權限測試用 `SET ROLE`。

## 語料管線

- **取得**：引擎負責下載、SHA-256、冪等與存檔；來源只實作 `Source`，在 `acquire/cli.py` 的 `SOURCES` 登記。
- **匯入**：`law.parse` 是純函式；整批比例過 `audit.LIMITS` 才寫入，否則結束碼 2。**改變解析輸出的修改必須遞增 `law.PARSER_VERSION`**，已匯入的法規才會以新版重寫。已匯入法規的內容改變是增量（`ingest/write.py` 的 `apply`）：更正以新增列取代舊列（`superseded_at`），查詢只看現行列。
- **檢索投影**（混合檢索）：字面臂與語意臂同列、以段落文字的雜湊為鍵，更正後無從陳舊；取不到查詢向量時只用字面臂，查詢不失敗。伺服器的詞典啟動時讀一次，**重建之後要重啟服務**。嵌入模型宣告在模型配置檔的 `[embedding]`，沒有這一段就只有字面臂。
- **字面臂略過高頻詞**：整句問題的 OR 查詢會被「之」「應」這類幾乎每段都有的詞撐成巨大的候選集，延遲與候選數成正比；`retrieval.prune_terms` 在查詢前略過段落比例超過 `ENVCITE_ONTOLOGY_LEXICAL_MAX_DF` 的詞，全是高頻詞時留最稀有的一個，不空查詢。詞頻表（`retrieval.term_stat`）隨檢索投影整批重算；沒有現行分詞器的統計時不修剪，查詢較慢但不出錯。改門檻預設要用 `tools/golden_retrieval_eval.py` 在開發集量測、以 `compare` 檢定，不憑感覺。
- **授權抽取**：`envcite-sync` 在匯入後跑 `delegation.extract`（規則、冪等）；**改解析規則要遞增 `delegation.RULE_VERSION`**。授權是 `annotation.assertion` 的子型別，語意與讀取見 README「規範關係：授權」。
- 分詞器的約束屬 `envcite-segmenter`（見其 README）。
- 結束碼的意義見 README；`2` 的情形是資料形狀不合、快照不存在、整批比例越線、嵌入端點設定錯誤（401／403、維度不符）。

## 標註、驗證與推論

- **沒有人審核標註**：陳述以 `extracted` 進入，經驗證閘門後依政策升為 `confirmed`，**只有已確認（或規則推導）的陳述參與推論**。
- **詞彙沒有上下位**（不借用 SKOS，載入帶 `broader` 的概念整份拒絕）：「甲屬於乙」的法律依據是立法定義或授權命令，不是詞彙的階層。立法定義用 `annotation.term_definition`，與陳述同為 `annotation.assertion` 的子型別；定義只在要件所屬的法規內使用，不支援準用。定義的確認要兩個互相獨立的方法都通過，任一失敗就駁回，沒有重讀者就不確認任何定義。外部對映（`vocab.concept_match`）是唯一的例外，只記「等同」：對到同一個外部識別碼的兩個概念，推論視為同一個。
- **載入規則**：每個陳述附它所讀的條文原文，載入時要求該單元現行的某個版本列確有這個雜湊，否則整份拒絕（結束碼 2）；狀態預設 `extracted`，載入不接受任何陳述；不確定法律概念或裁量的要件標 `judgement: true`（限值不得標）；門檻值的來源（字面值、授權條文、受控值、個別處分）恰有其一；責任基礎只屬主體槽位，認定方式只屬限值；一個罰則陳述一個效果，罰則陳述的種類必須是 `sanction`、所處罰的必須是 `prescriptive`，「命令屆期未履行」的前提必須是 `order`；一次載入是一個交易；每個主張寫完內容即封存（`annotation.seal`），封存後內容不可再補，修正是以 `set_confirm_state` 駁回再載入。
- **驗證**（`verification/`）：一個確定性閘門一個模組（`gates/`），模型閘門在 `model_gates.py`；每次結果寫一列 `annotation.verification`。`annotation.settle` 在必要方法最近一次都 pass 或 not_applicable、至少一個 pass、且沒有 fail 時升為 `confirmed`，有 fail 則 `rejected`。**審核狀態不論由哪條路徑改變都記一列 `policy`**。
  - **抽取者不得驗證自己的主張**（觸發器強制）；驗證者**預設也不得與抽取者同一家族**，合格者不足則 `inconclusive`。
  - **確定性閘門不證明語意忠於原文**。`envcite-verify` 用預設政策不接受任何陳述；只用確定性閘門接受要明說 `--require`。
  - 閘門失敗、逾時或模型無答案是 `inconclusive`，不是駁回、也不中斷。
- **模型配置**（檔案與用法見 README「模型配置」）：**家族由人明宣告**。**金鑰不入設定檔、不貼進對話**，設定檔不寫金鑰也不寫金鑰名：名稱由端點名推導（`ENVCITE_<端點名大寫>_API_KEY`），值依序從同名環境變數、作業系統鑰匙圈取（`verification/config.py` 的 `secret_name`、`resolve_secret`）；缺值的錯誤只列名稱。
  - **託管服務的模型名稱不保證是它所指的模型**：管線預設先對所有模型問固定的開放式問題，答案逐字相同就拒絕開跑（`SameModel`）；`--no-distinct-check` 略過。
  - 單元測試全用有名字與家族的替身，**沒有呼叫過任何模型**。
- 抽取者輸出的形狀不可信：形狀問題請模型修有限輪，修不好只丟那個陳述，不連累同單元其餘的陳述。
  義務的 duty 或 scope 借用本法已定義的泛稱（約定 G）也送回修復輪，但修不好不丟，交給驗證閘門。
- 本體的約定（只有規範性陳述有義務方向、授權不是許可、罰則一個效果一個陳述且引文只取該效果的子句、刑罰不是罰則、按次處罰是另一個帶「屆期仍未改善」要件的陳述）寫在 `verification/conventions.py` 一處，各提示都由它組成。
- **管線**可重跑、每個陳述 commit；`--pending` 的待辦由資料推得，所以重跑收斂；同一時間只跑一個（PostgreSQL 諮詢鎖）。保留樣本評估看過的法就用掉了，只報第一次看的計數，不報準確度。
- **行政規則**（`guidance` schema）：函釋、裁量基準另立一軸，**不進語料的位階、不是推論的前提**（行政程序法第 159 條），只附在推論結果旁（`Outcome.guidance`）。公文不可改，由人或規則載入（`derivation.method` 不得為 `llm`）；「解釋」錨定（單元，內容雜湊），條文文字改了就不再附上。
- **推論**（`reasoning/`）：通用推論程式 `rules.lp`（clingo）**不含任何法條**，也不用模型；案件事實由呼叫端給。結論都是**候選**；缺的事實、需判斷的要件、矛盾的事實、未決的衝突與罰則競合分開回報。
  - `order`（預防性處分）與 `disgorgement`（不法利得的追繳）不是行政罰，行政罰法總則的排除、減輕、時效與一行為都不適用；罰則的 `discretionary` 與要件的 `judgement` 是兩個層次。以另一效果為前提的罰則（`sanction_trigger`），前提列出才列出，前提附條件則它也附條件。
  - 呼叫端看得到的語意（未給的許可量值是未知、認定方式不符不逕判、`evaluate_across` 不選「最有利」、`uncovered_laws`）以倉庫根 `proto/README.md`〈標註與推論〉為準，改推論時一起看。
  - **改 `rules.lp` 要跑 `tests/test_reasoning*.py`**（真實 schema、不用替身）。

## Contract

- 契約是倉庫根的 `proto/envcite/ontology/v1/ontology.proto` 與 `reasoning.proto`；錯誤原因清單在 `proto/README.md`。
- 資料不分租戶；`x-tenant-id`／`x-request-id` metadata 只用於日誌與稽核。
- `ReasoningService` 唯讀。訊息強型別：列舉不用字串、數值用 `Decimal`（十進位文字，不用浮點）、`AdministrativePenaltyFacts` 用 `optional`。日期維持 ISO 8601 字串。
- 錯誤一律 `errors.ServiceError`，由 `errors.maps_errors` 在 servicer 邊界轉成 `google.rpc.Status`；呼叫端依 `reason` 分流，不解析訊息。新增錯誤是新增常數，不改現有 reason 的意思。
- 同步伺服器的執行緒池是固定的：`Evaluate` 有自己的並行名額，期限同時成為資料庫的 `statement_timeout`。gRPC 在呼叫沒有期限時 `time_remaining()` 回極大的數，不是 `None`，一律經 `envcite_grpc.remaining_seconds` 轉換。
- 連線選項成套放在 `envcite-grpc` 的 `connection.py`，改 keepalive／ping 前先看該套件的 README。

## Safety

- 全國法規資料庫的資料必須標示出處（政府資料開放授權條款－第 1 版）。測試資料（`tests/sample.py`）節錄自它。

## Done when

- ruff、mypy、pytest 通過。
