# AGENTS.md

倉庫層規範見根 `AGENTS.md`；本檔只管 `services/ontology/`。

## Project

- **Name:** envcite-ontology（Python 套件 `envcite_ontology`）
- **Role:** 本體服務（shared, non-tenant data）：各來源的知識集中在本服務，一種來源一個本體，各有自己的 schema 與引用代號字母（呼叫端看到的規則在倉庫根 `proto/README.md`）。新增來源是新增一個本體，不另立服務；拆成獨立服務只在該本體需要獨立擴縮或發版、資料不能與共享資料同庫、或需要不同的執行環境。由 `services/platform` 與 `services/consult` 呼叫。
- **Layout:** 取得引擎、投影、驗證管線、gRPC 伺服器，以及本體的來源、解析、規則，都在套件頂層。資料表歸該本體自己的 schema。
- **Stack:** Python · uv · gRPC（grpcio，同步伺服器）· PostgreSQL（psycopg 3）· ruff · mypy (strict) · pytest
- **Data model:** **關聯式實表，PostgreSQL 是唯一真值**，表結構以 `migrations/*.sql` 為準。法規以（管轄，識別碼方案，在地識別碼）為鍵——管轄是封閉的列舉（中央與 22 縣市），方案與管轄的配對由 CHECK 強制（中央方案只配中央、地方方案只配縣市；地方方案是契約預留，目前沒有取得來源），兩者都沒有預設值，寫入者與呼叫端都得明說；新增識別碼方案要在同一個遷移裡說明它屬於哪一級；效力逐條文單元記載、逐單元解析；法規名稱可因修正而變，標題屬公布版本；標註錨定（單元，內容雜湊），不錨定版本列。**時間軸的不變式由綱要強制，不靠匯入後的檢查**：版本不重疊（`WITHOUT OVERLAPS`）、不留縫（文字時間的上界以延遲外鍵參照下一版的版本日）、施行規則決定起點與版本日的關係（CHECK；早於版本日只能是明文的 `retroactive`）；查不到全文的版本仍是一版（`text_status = unavailable`）。新規則先寫成約束，程式與測試跟著約束。

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

- **程式風格**（Python ≥3.14，ruff 的 `target-version` 在倉庫根 `pyproject.toml`，UP 規則據此要求新語法）：
  - 不寫 `from __future__ import annotations`（註解延後求值）；泛型用 PEP 695（`def f[T]`），別名用 `type X = …`。
  - 提示詞不寫在 Python 裡：模板在 `verification/templates/*.j2`（Jinja2），渲染只經 `verification/prompts.py` 的型別化函式。共同骨架用繼承（`base.j2` → `with_conventions.j2`／`definition.j2` → 各提示），「有才放」的區塊是 `macros.j2` 的巨集；改提示詞只改模板，要在開發集上量測，不憑感覺。
  - 讀資料庫列用 `db.fetch`／`fetch_one`／`scalar`，每個查詢一個私有的具名列型別（`NamedTuple`，欄位次序同 SELECT；可為 NULL 的欄位宣告 `| None`，依 `migrations/` 查證，不猜），不在用處 `cast`。
  - `verification/__init__.py` 不匯出任何名稱：從定義它的子模組匯入（匯出會讓匯入任何一個子模組都載入整條驗證管線）。
  - 函式的大小由 ruff 守門（根 `pyproject.toml`：圈複雜度與分支各 14、語句 60）；超過就拆成有名字的小函式，不加 `noqa`、不放寬門檻。
  - `Any` 只留在動態的邊界（JSON 文件、clingo 的答案集原子、唯讀連線的列），並在原處說明；其餘用精確的型別，protobuf 訊息標註實際的 `pb.*` 類別，不用 `Any`。
  - 人寫的 JSON 文件在邊界以 pydantic 模型驗證並解碼成型別化的結構，內部不傳 `Mapping[str, Any]`；新增的文件載入器照 `ruling.py` 的做法。
  - 資料庫連線的型別只有 `db.Conn`；匯入問題（`source.import_problem`）的型別與寫入只有 `import_problems`，各來源的匯入都經它。
  - 註解：模組、類別與公開函式寫 docstring，首句一句話說「是什麼」，其餘只寫前提、不變式與「為什麼」，不複述程式做了什麼；欄位與常數的說明用前置的 `#:`。
- **環境變數以 `src/envcite_ontology/settings.py` 為唯一來源**；入口不直接讀 `os.environ`；加變數時同步 `.env.example`（`tests/test_settings.py` 守門）。
- 測試用**真實的 PostgreSQL**：設 `ENVCITE_ONTOLOGY_TEST_DSN`，或留空讓 testcontainers 起一個（需要 Docker daemon）。不用替身，因為本服務一半的讀取邏輯在 SQL 裡。
- **綱要由 `migrations/*.sql` 依檔名順序套用。已套用的檔案不可修改**——要改就新增一個檔，由 `schema_migration` 的雜湊比對強制。
- **資料庫角色**（定義在 `migrations/` 的角色遷移）：`reader`（伺服器）、`writer`（取得、匯入、投影、標註管線）、`reviewer`（writer 加手動審核與載入已審核的主張）。標註層對寫入者**只能新增**：主張寫入後內容不改不刪，驗證紀錄只經 `record_verification`、接受只經 `settle`、手動審核只經 `set_confirm_state`（都是 `SECURITY DEFINER`）。角色由部署庫建立，遷移只授權；權限測試用 `SET ROLE`。

## 語料管線

- **取得**：引擎負責下載、SHA-256、冪等與存檔；來源只實作 `Source`，在 `acquire/cli.py` 的 `SOURCES` 登記。
- **匯入**：`law.parse` 是純函式；整批比例過 `audit.LIMITS` 才寫入，否則結束碼 2。**改變解析輸出的修改必須遞增 `law.PARSER_VERSION`**，已匯入的法規才會以新版重寫。已匯入法規的內容改變是增量（`ingest/write.py` 的 `apply`）：更正以新增列取代舊列（`superseded_at`），查詢只看現行列。
- **歷史版本**（`envcite-history <來源>`）：一部法規只有一個來源，依種類分（法律取自立法院、命令取自主管機關），`history_sources.py` 一處決定。只補首次取得官方資料庫快照之前的版本，由最早的官方版本往舊逐版回推，接不上的那一版與更早的都寫成查不到全文的版本，不跳過缺口、不以他版文字頂替；與較新版本正規化後相同的條沿用較新版本的文字。匯入每次對所有快照重算、只寫差異，回推規則改了不必遞增版本；已寫入的歷史版本日不在新目標裡時整部不寫（`history_reshaped`），待人查看。
- **檢索投影**（混合檢索）：字面臂與語意臂同列、以段落文字的雜湊為鍵，更正後無從陳舊；取不到查詢向量時只用字面臂，查詢不失敗；每條命中另回「查詢詞覆蓋率」（見「查詢詞覆蓋率」）。伺服器的詞典啟動時讀一次，**重建之後要重啟服務**。嵌入模型宣告在模型配置檔的 `[embedding]`，沒有這一段就只有字面臂。
- **字面臂略過高頻詞**：整句問題的 OR 查詢會被「之」「應」這類幾乎每段都有的詞撐成巨大的候選集，延遲與候選數成正比；`retrieval.prune_terms` 在查詢前略過段落比例超過 `ENVCITE_ONTOLOGY_LEXICAL_MAX_DF` 的詞，全是高頻詞時留最稀有的一個，不空查詢；覆蓋率只算留下來的詞（`OntologyStore.searched_terms`）。詞頻表（`retrieval.term_stat`）隨檢索投影整批重算；沒有現行分詞器的統計時不修剪，查詢較慢但不出錯。改門檻預設要用 `tools/golden_retrieval_eval.py` 在開發集量測、以 `compare` 檢定，不憑感覺。
- **查詢詞覆蓋率**：字面臂是各詞的 OR，只含一個詞的條文與含全部詞的字面分數差不多，語料裡沒有任何一條含完整詞組時，這些片段命中反而排最前面並通過下界。`SearchArticles` 因此對每條命中另回 `term_coverage`（`coverage.term_coverage`：字面臂實際查的詞有多少比例出現在條文全文），**不改 `lexical_score` 與名次**。不要把覆蓋率乘進字面分數：字面分數的尺度是 consult 相關性下界的校準對象，乘進去整個尺度下移，原下界下可答題的通過率大幅下降，而可答與無答案的取捨沒有改善（開發集量測，見 `eval/lexical-coverage-2026-11-13.md`）。覆蓋率只給要「排除只沾到一個詞的命中」的呼叫端（platform 的搜尋頁與 `/v2/search`）。
- **函釋帶出條文**：使用者查的詞常是函釋的用語（函釋寫「露天燃燒」、條文寫「燃燒」），兩臂都找不到，解釋它的函釋卻已指明是哪一條。請求帶 `include_guidance` 時，`SearchArticles` 另以 `retrieval.guidance_articles`（`guidance_search.py`）找主旨或內文含查詢每個詞組（以空白分，最多 5 個）、在查詢日有效的函釋所解釋的條文，與兩臂的結果以名次倒數融合，最多比 `limit` 多回 5 條，並回 `guidance_documents`。**函釋只用來找條文，不是推論的前提**：回傳的是現行條文，條文與函釋的錨定判斷同 `guidance.for_statements`（錨定的內容雜湊在查詢日仍現行、發文日不晚於查詢日、尚未停止適用）。**常見詞不當入口**：命中的函釋占可用函釋（查詢日已發文、尚未停止適用、至少有一個錨定）的比例超過 `ENVCITE_ONTOLOGY_GUIDANCE_MAX_DF` 時，這個查詢整個不用函釋入口，不挑其中幾件——詞組出現在那麼多函釋裡，就指不出哪幾條。staging 上有鑑別力的詞組（露天燃燒、噪音、繞流排放）約 1%，常見詞（固定污染源、水污染防治、廢棄物清理）在 7% 以上，預設取其間；樣本小、沒有以函釋用語查詢的題可量，改預設值要先有題。預設不納入，不影響其他呼叫端；詞組是子字串比對、沒有索引，每次約 150 ms（staging 3.5 萬件函釋），所以只給要它的呼叫端（platform 的搜尋頁與 `/v2/search`，以及 consult：`guidance_documents` 大於 0 的條文不必字面或語意達標就進證據集，但不算高信心），函釋量大到不夠快時要先建分詞索引。
- **問句點名法規則縮小範圍**：問句含法規全名（`law_names.LawNames`，標題取自資料庫所有版本，三字以上、由長到短不重疊）時，`search_hybrid` 的兩臂只在那些法規裡找（`p_resources`；語意臂在範圍內改精確掃描，HNSW 先取近鄰再過濾會落空）；範圍是被點名的法規加上沿授權（`annotation.delegated_by`，命令授權的命令也算）訂定的命令，由 `retrieval.law_scope` 在資料庫內算；`search_hybrid` 內嵌後會把傳入的運算式代入每個使用處，所以範圍先在 MATERIALIZED 的 `scope` CTE 算一次，改這個函式不可拿掉。點名的法規沒有任何命中才改找全庫。命中義務條（或授權命令的條文）時，`retrieval.search_expanded` 緊接著帶出處罰它的罰則條（取自 `annotation.current_violation`，分數取自帶出它的那一條）。名稱表和詞典一樣啟動時讀一次，匯入新法規後要重啟。門檻與比對規則的改動要用 `tools/golden_retrieval_eval.py` 在開發集量測（`--no-law-names` 為不縮小的對照）、以 `compare` 檢定。
- **精排**（`reranking.py`、`service.py` 的 `_rerank`）：請求帶 `rerank` 且 `models.toml` 有 `[reranker]` 時，兩臂（與函釋）取 `ENVCITE_ONTOLOGY_RERANK_CANDIDATES` 條候選，交給端點的 `/rerank` 逐條打分，依分數取前 `limit` 條（同分依精排前的名次），並回 `rerank_score`。**精排是加強，不是查詢成功的條件**：名額滿（`RERANK_SLOTS`，不等待）、逾時（`RERANK_TIMEOUT` 與呼叫端剩下的期限取小者）、端點錯誤、回應缺漏或重複都退回精排前的次序（一樣只取前 `limit` 條）並不設分數，不重試、不回錯誤；半套的分數整份不收。函釋帶出而被擠出前 `limit` 條的條文照 `fuse` 的契約補回（最多 `EXTRA` 條），因為精排只看（查詢，條文）一對，看不到函釋這個依據。**指標**（`telemetry.RerankMetrics`）：`envcite.ontology.rerank.outcomes`（帶 `rerank` 且有候選的請求各算一次，`outcome` 為 `reranked`、`busy`、`error`、`unconfigured`；後三者都是降級）與 `envcite.ontology.rerank.duration`（對端點打分的耗時，含分批；`ok`、`error`）。成功的精排不寫日誌，降級的比率與端點的耗時分布只看這兩個指標；標籤只用這幾個值。端點行為以實測為準：`top_n`、`return_documents` 被忽略、回應依分數排序並回傳文件原文、空清單回 500，所以用戶端依 `index` 還原次序、不送空清單；**一次最多 32 條，33 條起 HTTP 500（還可能掛住）**，所以用戶端每 30 條（`MAX_BATCH`）一批、各批共用同一份時間預算，任一批失敗整份失敗。**分數是 sigmoid 後的機率，可跨查詢比較，但門檻是模型條件化的值**：consult 與 platform 的相關性下界仍是字面與語意分數，不讀 `rerank_score`；要拿它當可答性門檻，先用 `tools/golden_retrieval_eval.py measure --rerank` 在開發集量測 negative 桶的分布，再以 `compare` 檢定，不憑感覺。`ENVCITE_ONTOLOGY_RERANK_CANDIDATES` 的預設值 30 在原生題庫開發集（字面臂）上量過：10、20、30 每一步都顯著更好，更深（要分批、延遲加倍）沒量；混合臂加精排要補嵌入才量得到。效益、延遲分布與可答性門檻的分析見 `eval/rerank-effect-dev-2026-11-13.md`。
- **原生題庫**（`tools/native_golden.py`、`eval/native-golden-*.json`）：題目與答案只由語料的條文結構程式化產生（定義條、罰則的「違反第 N 條」、命令第一條的訂定依據、「準用第 M 條」、無答案詞），**不用模型出題、不取答案於被測的檢索系統**，條文以語料的法規代碼加條號為鍵，不依外部題庫。每題帶產生規則與原文證據，`verify` 逐題重核並用不同的規則另掃遺漏；改產生規則的輸出要遞增 `GENERATOR_VERSION`、重建與重新 `derive`，舊題庫的 qrels 與切分以雜湊作廢。**不要為了讓 `verify` 乾淨而放寬或收緊文法**：不符是題庫與語料不一致的訊號，先看是哪一邊錯。題量被每類的上限（`CAP`）截斷，候選池比它大。
- **授權抽取**：`envcite-sync` 在匯入後跑 `delegation.extract`（規則、冪等）；**改解析規則要遞增 `delegation.RULE_VERSION`**。授權是 `annotation.assertion` 的子型別，語意與讀取見 README「規範關係：授權」。
- 分詞器的約束屬 `envcite-segmenter`（見其 README）。
- 結束碼的意義見 README；`2` 的情形是資料形狀不合、快照不存在、整批比例越線、嵌入端點設定錯誤（401／403、維度不符）。

## 標註、驗證與推論

- **沒有人審核標註**：陳述以 `extracted` 進入，經驗證閘門後依政策升為 `confirmed`，**只有已確認（或規則推導）的陳述參與推論**。
- **詞彙沒有上下位**（不借用 SKOS，載入帶 `broader` 的概念整份拒絕）：「甲屬於乙」的法律依據是立法定義或授權命令，不是詞彙的階層。立法定義用 `annotation.term_definition`，與陳述同為 `annotation.assertion` 的子型別；定義只在要件所屬的法規內使用，不支援準用。定義的確認要兩個互相獨立的方法都通過，任一失敗就駁回，沒有重讀者就不確認任何定義。外部對映（`vocab.concept_match`）是唯一的例外，只記「等同」：對到同一個外部識別碼的兩個概念，推論視為同一個。
- **載入規則**：每個陳述附它所讀的條文原文，載入時要求該單元現行的某個版本列確有這個雜湊，否則整份拒絕（結束碼 2）；狀態預設 `extracted`，載入不接受任何陳述；不確定法律概念或裁量的要件標 `judgement: true`（限值不得標）；門檻值的來源（字面值、授權條文、受控值、個別處分）恰有其一；責任基礎只屬主體槽位，認定方式只屬限值；一個罰則陳述一個效果，罰則陳述的種類必須是 `sanction`、所處罰的必須是已確認的 `prescriptive`（連不上的存成待連結罰則，見 README「規範關係：罰則的連結」），「命令屆期未履行」的前提必須是 `order`；一次載入是一個交易；每個主張寫完內容即封存（`annotation.seal`），封存後內容不可再補，修正是以 `set_confirm_state` 駁回再載入。
- **驗證**（`verification/`）：一個確定性閘門一個模組（`gates/`），模型閘門在 `model_gates.py`；每次結果寫一列 `annotation.verification`。`annotation.settle` 在必要方法最近一次都 pass 或 not_applicable、至少一個 pass、且沒有 fail 時升為 `confirmed`，有 fail 則 `rejected`。**審核狀態不論由哪條路徑改變都記一列 `policy`**。
  - **抽取者不得驗證自己的主張**（觸發器強制）；驗證者**預設也不得與抽取者同一家族**，合格者不足則 `inconclusive`。
  - **確定性閘門不證明語意忠於原文**。`envcite-verify` 用預設政策不接受任何陳述；只用確定性閘門接受要明說 `--require`。
  - 閘門失敗、逾時或模型無答案是 `inconclusive`，不是駁回、也不中斷。
- **模型配置**（檔案與用法見 README「模型配置」）：**家族由人明宣告**。**金鑰不入設定檔、不貼進對話**，設定檔不寫金鑰也不寫金鑰名：名稱由端點名推導（`ENVCITE_<端點名大寫>_API_KEY`），值依序從同名環境變數、作業系統鑰匙圈取（`verification/config.py` 的 `secret_name`、`resolve_secret`）；缺值的錯誤只列名稱。
  - **託管服務的模型名稱不保證是它所指的模型**：管線預設先對所有模型問固定的開放式問題，答案逐字相同就拒絕開跑（`SameModel`）；`--no-distinct-check` 略過。
  - 單元測試全用有名字與家族的替身，**沒有呼叫過任何模型**。
  - **黃金測試集的相關性判定者**（`[relevance]`，`tools/relevance_judge.py`）只排除受測路徑（檢索與作答）的家族 `system_families`；標註管線的角色是離線的，不算受測系統。判定者至少兩個、彼此不同家族；判定前以 `SameModel` 探測別名，閘道回覆的模型名不符即視為失敗。提示只在開發集上調，`pool` 要求同一提示雜湊的開發集校準檔。
- **義務的主體與上位條件由程式負責，不靠抽取者或審查者**：主體值要是原文寫出的行為人，否則記成「未明示」（`subject.normalize`；被規範的物與行為、憑空補的「任何人」都不是義務人）；引文之前同一句或上位導言的條件子句，陳述沒涵蓋就由 `carry.carry` 補成 `carried` 的 scope。兩者的形狀由資料庫強制（`statement_slot.unstated`、`statement_condition.carried` 的 CHECK 與觸發器）；旗標只由程式標，模型寫的 `carried`、非主體槽位的 `unstated` 在 `tidy` 去掉。
- 抽取者輸出的形狀不可信：形狀問題請模型修有限輪，修不好只丟那個陳述，不連累同單元其餘的陳述。
  義務的 duty 或 scope 借用本法已定義的泛稱（約定 G）也送回修復輪，但修不好不丟，交給驗證閘門。
  規範性陳述的引文缺了助動詞而單元原文有（從規範句切下的子句）是形狀問題，修不好就丟；
  義務引文裡助動詞前後有字沒被 scope、主體或 duty 要件涵蓋（漏了期限、對象、限定語）只送回修復，不丟。
- **重抽**：義務沒有定論的單元（`annotation.reextraction_due`）帶審查者指出的問題重抽；輪數上限是 `extraction_attempt.round` 的 CHECK，不是程式裡的常數，該不該重抽由資料推得、不另存佇列，所以中斷後重跑自己收斂。重抽只改變誰被抽到、不放寬閘門：確認仍要所有必要方法通過。量測與已知缺口見 `eval/duty-reextraction-2026-11-06.md`，兩型瑕疵修復的獨立量測見 `eval/duty-fix-effect-2026-11-09.md`。
- 本體的約定（只有規範性陳述有義務方向、授權不是許可、罰則一個效果一個陳述且引文只取該效果的子句、刑罰不是罰則、按次處罰是另一個帶「屆期仍未改善」要件的陳述）寫在 `verification/templates/conventions.j2` 一處，各提示都由它組成。
- **管線**可重跑、每個陳述 commit；`--pending` 的待辦由資料推得，所以重跑收斂；同一時間只跑一個（PostgreSQL 諮詢鎖）。保留樣本評估看過的法就用掉了，只報第一次看的計數，不報準確度。
- **行政規則**（`guidance` schema）：函釋、裁量基準另立一軸，**不進語料的位階、不是推論的前提**（行政程序法第 159 條），只附在推論結果旁（`Outcome.guidance`）。公文不可改，由人或規則載入（`derivation.method` 不得為 `llm`）；「解釋」錨定（單元，內容雜湊），條文文字改了就不再附上。取得（`envcite-guidance sync`）只經 `guidance.load` 寫入，**改變寫入內容或錨定規則要遞增 `guidance_sync.RULE_VERSION`**。
- **推論**（`reasoning/`）：通用推論程式 `rules.lp`（clingo）**不含任何法條**，也不用模型；案件事實由呼叫端給。結論都是**候選**；缺的事實、需判斷的要件、矛盾的事實、未決的衝突與罰則競合分開回報。
  - `order`（預防性處分）與 `disgorgement`（不法利得的追繳）不是行政罰，行政罰法總則的排除、減輕、時效與一行為都不適用；罰則的 `discretionary` 與要件的 `judgement` 是兩個層次。以另一效果為前提的罰則（`sanction_trigger`），前提列出才列出，前提附條件則它也附條件。
  - 呼叫端看得到的語意（未給的許可量值是未知、認定方式不符不逕判、`evaluate_across` 不選「最有利」、`uncovered_laws`）以倉庫根 `proto/README.md`〈標註與推論〉為準，改推論時一起看。
  - 違憲失效的版本不是依據：陳述與限值的解析在 SQL 排除它，被排除的另列（`invalidated`），不悄悄消失。命令的授權鏈斷了是「授權依據待查」，罰則附條件，不是不成立。
  - 行政罰法第 18、26 條只給候選與額度，不選罰鍰額；刑事優先只擋罰鍰（與法院已宣告沒收時的沒入），是否觸犯刑事法律沒給就不套用。金額不進 clingo（32 位元整數放不下千分位的金額），比較與加總在 `facts.amounts`。
  - **改 `rules.lp` 要跑 `tests/test_reasoning*.py`**（真實 schema、不用替身）。

## Contract

- 契約是倉庫根的 `proto/envcite/ontology/v2/ontology.proto` 與 `reasoning.proto`；錯誤原因清單在 `proto/README.md`。法規的自然鍵三欄必填，缺少就是 `INVALID_ARGUMENT`，不補預設。
- 資料不分租戶；`x-tenant-id`／`x-request-id` metadata 只用於日誌與稽核。
- `ReasoningService` 唯讀。訊息強型別：列舉不用字串、數值用 `Decimal`（十進位文字，不用浮點）、`AdministrativePenaltyFacts` 用 `optional`。日期維持 ISO 8601 字串。
- 錯誤一律 `errors.ServiceError`，由 `errors.maps_errors` 在 servicer 邊界轉成 `google.rpc.Status`；呼叫端依 `reason` 分流，不解析訊息。新增錯誤是新增常數，不改現有 reason 的意思。
- 同步伺服器的執行緒池是固定的。請求路徑上的讀取與 `Evaluate` 把剩餘期限同時當成連線池等待上限與 `statement_timeout`；等不到連線回 `BUSY`，讀取查詢被取消回 `READ_TIMEOUT`（`Evaluate` 仍是 `EVALUATION_TIMEOUT`）。`Evaluate` 另有自己的並行名額。查詢嵌入的名額是 `server.py` 的 `QUERY_EMBED_SLOTS`，與 `WORKERS` 並列，滿了就略過語意臂。gRPC 在呼叫沒有期限時 `time_remaining()` 回極大的數，不是 `None`，一律經 `envcite_grpc.remaining_seconds` 轉換。
- 連線選項成套放在 `envcite-grpc` 的 `connection.py`，改 keepalive／ping 前先看該套件的 README。

## Safety

- 全國法規資料庫的資料必須標示出處（政府資料開放授權條款－第 1 版）；環境部主管法規查詢系統的歷史版本亦同（環境部網站資料開放宣告，CC BY 4.0）；立法院法律系統的歷史版本亦同（條文不得為著作權標的，比照立法院網站資料開放宣告註明出處；該站只接受舊式 TLS 重新協商，取得時循序、禮貌間隔）。測試資料（`tests/sample.py`）節錄自它。
- 公報（政府資料開放授權條款－第 1 版）與環境部函釋（環境部網站資料開放宣告，CC BY 4.0）同樣須標示出處；快照的 `license_notice` 記著。函釋網站沒有使用條款與 robots.txt，取得維持循序與禮貌間隔，不並行。

## Done when

- ruff、mypy、pytest 通過。
