<!--
type: reference
applies-to: all
owns: [canon-entrypoints, sync-exit-codes, models-config, norm-relations, competency-registry, golden-test-collection, judicial-credentials, law-suspension]
-->

# canon

環境知識的集中處：各來源的知識各成一個來源（如法規 `law`），在這裡提供語料、檢索與推論。回[倉庫總覽](../../README.md)。

- **介面**：gRPC `CanonService`（法規與檢索）、`ReasoningService`（標註與推論）；契約 [`canon.proto`](../../proto/envcite/canon/v1/canon.proto)、[`reasoning.proto`](../../proto/envcite/canon/v1/reasoning.proto)；約定與錯誤原因見 [`proto/README.md`](../../proto/README.md)。
- **呼叫端**：[`platform`](../platform/README.md)、[`consult`](../consult/README.md)。資料全域共用，不分租戶；PostgreSQL 為唯一真值。
- **管線分工**：語料與檢索由 `canon sync` 依序取得、匯入、投影；標註由 `canon pipeline` 以語言模型抽取並經閘門驗證；推論（clingo）不用模型，只取已確認的標註，結論都是候選。
- **共用套件**：[`envcite-segmenter`](../../libs/python/envcite-segmenter/README.md)、[`envcite-grpc`](../../libs/python/envcite-grpc/README.md)、[`envcite-proto`](../../libs/python/envcite-proto/README.md)。

## 概念

| 組 | 節 | 內容 |
|----|----|------|
| 入口 | [入口](#入口) | 同一映像裡的伺服器、同步、歷史、標註、行政規則、違憲失效 |
| 語料 | [歷史版本](#歷史版本) | 法律自立法院，命令自環境部 |
| 語料 | [停止與恢復適用](#停止與恢復適用) | 停止適用中的條不是有效規範 |
| 語料 | [行政規則的取得](#行政規則的取得) | 公報與環境部函釋 |
| 語料 | [裁判書帳密](#裁判書帳密) | 司法院帳號的檔案、路徑與權限 |
| 語料 | [違憲失效](#違憲失效) | 憲法法庭宣告失效的條文 |
| 標註與規範 | [標註管線](#標註管線) | 抽取、驗證、接受；重抽；主體與上位條件 |
| 標註與規範 | [模型配置](#模型配置modelstoml) | 端點與金鑰名。金鑰怎麼找見 [`CONFIGURATION.md`](../../CONFIGURATION.md) |
| 標註與規範 | [規範關係：授權](#規範關係授權) | 命令依哪一條法律訂定 |
| 標註與規範 | [規範關係：罰則的連結](#規範關係罰則的連結) | 罰則連到已確認的義務 |
| 評估 | [黃金測試集](#黃金測試集) | 檢索召回與拒答 |
| 評估 | [涵攝評估](#涵攝評估) | 三值涵攝的案例 |
| 評估 | [能力問題登記](#能力問題登記) | 每題的可答狀態在 `competency.toml` |

## 入口

所有入口都在同一個映像裡：預設 command 是伺服器，其餘以 command 覆寫。單一指令為 `canon <子指令>`（與 `python -m canon` 等價），子指令名稱登記在 [`cli.py`](src/canon/cli.py) 的 `SUBCOMMANDS`。

| 入口 | 型態 | 做什麼 |
|------|------|--------|
| `canon serve` | 長駐 | gRPC 伺服器 |
| `canon migrate` | 一次性 | 套用 `migrations/*.sql` |
| `canon sync <來源>` | 排程 | 取得、匯入、授權抽取、檢索投影，四步冪等 |
| `canon acquire <來源>` | 一次性 | 只取得快照 |
| `canon import <來源>` | 一次性 | 快照寫入語料；`--dry-run` 只解析與驗收 |
| `canon history <ly\|moenv> [sync\|acquire\|import]` | 排程 | 法規歷史版本：取得、回推、授權抽取、檢索投影 |
| `canon guidance sync <來源>` | 排程 | 取得行政規則寫入 `guidance`（`gazette` 公報、`moenv` 環境部函釋） |
| `canon guidance load <檔>` | 一次性 | 載入人寫的行政規則 JSON；格式見 [`law/guidance.py`](src/canon/law/guidance.py) |
| `canon ruling fetch`、`load <檔>` | 一次性 | 取得或載入違憲失效裁判 |
| `canon judgment sync` | 排程 | 取行政法院裁判異動；全文是 PDF 時抽文字層入庫；掃描頁在宣告 `[ocr]` 時逐頁 OCR（文字層夠的頁不送），否則只記 `file_only`。帳密見[裁判書帳密](#裁判書帳密) |
| `canon pipeline` | 排程 | 標註：抽取、驗證、接受 |
| `canon annotate <檔>` | 一次性 | 載入一份人寫的標註 JSON |
| `canon verify` | 一次性 | 只跑確定性閘門的驗證；`--require <方法,…>` |

- **來源與工具**：`<來源>` 登記在 [`acquire/cli.py`](src/canon/acquire/cli.py) 的 `SOURCES`（`moj` 為全國法規資料庫）。開發工具在 [`tools/`](tools)，不進映像。
- **環境變數**：以 [`settings.py`](src/canon/settings.py) 為準，範本是 `.env.example`。
- **結束碼**（`canon sync`、`acquire`、`import`）：`0` 成功或無事可做、`1` 暫時性失敗（可重試）、`2` 重試無益（資料不合預期，或嵌入端點拒絕認證、維度不符）。
- **重試政策**：下載與嵌入之重試、退避與斷路器以 `ENVCITE_JOBKIT_<欄位>` 覆寫（欄位見 [`envcite-jobkit`](../../libs/python/envcite-jobkit/README.md#政策欄位)，預設見 [`sync.py`](src/canon/sync.py) 的 `POLICY`）。端點錯誤退避重試；嵌入斷路器開啟時原地等到半開，重試用盡或內容被拒的批次跳過留待下次補做。

## 語料

### 歷史版本

全國法規資料庫 API 僅有現行版本。每次修正前的完整版本依種類取自制定或主管機關，**一部法規只有一個來源**（[`law/history_sources.py`](src/canon/law/history_sources.py)）；其他主管機關的命令自首次取得全國法規資料庫快照起累積。

| 來源 | 種類 | 系統 | 授權 | 快照 feed／鍵 | `text_authority` |
|------|------|------|------|---------------|------------------|
| `ly` | 法律 | 立法院法律系統（[`law/ly_law.py`](src/canon/law/ly_law.py)） | 不得為著作權標的（著作權法 §9）；**須標示出處** | `ly_law`／法規代碼 | `legislature` |
| `moenv` | 命令 | 環境部主管法規查詢系統（[`law/moenv_law.py`](src/canon/law/moenv_law.py)） | CC BY 4.0；**須標示出處** | `moenv_law`／系統代碼 | `competent_authority` |

1. **取得（`canon history <來源> acquire`）**：一部法規打包為一份快照，過去版本不重抓（`--rescan` 全部重抓）。`ly` 依現行法名稱檢索並抓沿革與各版條文；`moenv` 走分類樹列命令並抓歷史清單與各頁。
2. **匯入（`canon history <來源> import`，須先跑過 `canon sync moj`）**：
   - 依鍵（`ly`）或名稱與種類（`moenv`）對齊語料，來源現行日須與語料最新版同日。
   - 由語料最早版本往舊回推（[`law/backfill/`](src/canon/law/backfill/)），版本日為公布／發布日；相鄰兩版區間首尾相接。
   - 缺全文或解析不出處停在該版，該版與更早公布寫成 `text_status = unavailable`，時間軸仍接到制定。
   - 更換來源時以新來源文字取代同日版本列；已寫入版本日不在新目標內則整部不寫（`history_reshaped`）；寫入遭 DB 約束擋下記 `history_write_rejected` 並略過該部。
3. **讀取**：不另立介面，由 `corpus.provisions_at` 與依日檢索沿同一條時間軸逐條解析。
4. **結束碼**：`0` 成功；`1` 暫時性錯誤（含隔離超過 1%）；`2` 資料不合預期、比例越線或寫入被拒超限；`75` 斷路器開啟。問題記於 `source.import_problem`。

### 停止與恢復適用

中央法規標準法第 19 條：法規得暫停適用其一部或全部；停止或恢復適用之程序，準用廢止或制定之規定。沿革裡的停止適用（`suspension`）與恢復適用（`resumption`）記於 `corpus.lifecycle_event`，歸類在 [`law/history.py`](src/canon/law/history.py)。

| 欄 | 內容 |
|----|------|
| `effective_on` | 生效日：原文的起日（「自即日起」為發文日，「自發布日」起算至第三日）。停止適用沒寫起日者，準用第 22 條第 3 項自公布或發布之日起算至第三日；恢復適用沒寫起日者不推定第 13 條，記為 `unknown` |
| `articles` | 所及的條（`art_N`），NULL 為整部。部分修正也記所列的條，供下面第 3 點判斷 |

1. **範圍**：只認整部與列出的條。範圍或終點不明者（沒列條號的「關於某某條文」、條以下的但書、附期限的暫停）與只記立法院通過者，記為 `unknown`、保留原文。一筆裡有修正也有停止適用時，種類是修正。
2. **讀取**：`corpus.provisions_at` 對停止適用中的條回 `suspended`：文字與現行版本都在，但不是有效規範。只取 `in_force` 的讀取（推論的陳述、限值來源、授權鏈、依日列條）因此不含它；恢復適用生效之日起回到原狀態。
3. **之後的新文字**：停止適用只及於當時的條文。之後公布或發布而觸及該條的文字（全文修正，或列出該條的部分修正）依自己的施行日生效，不在停止適用中（`corpus.suspended`），與[違憲失效](#違憲失效)不及於裁判後修正的新文字同理。
4. **沒有生效日的停止適用列不生效力**：範圍沒有記下，不能當成整部；解析器重匯時補上。

### 裁判書帳密

`canon judgment sync` 的司法院帳號不走環境變數，也不進 [`settings.py`](src/canon/settings.py)。本機與叢集是同一份 JSON、同一條權限規則，只是檔案放的位置不同。實作在 [`law/judicial_credentials.py`](src/canon/law/judicial_credentials.py)。

| 順序 | 路徑 | 誰放 |
|------|------|------|
| 1 | `/var/run/canon/judicial.json` | 叢集把密鑰卷掛在這裡。有這個檔就用它 |
| 2 | 服務目錄 `judicial.local.json` | 本機。版控忽略。範本是 [`judicial.local.json.example`](judicial.local.json.example) |

```json
{"user": "<平臺帳號>", "password": "<密碼>"}
```

帳號去掉前後空白，密碼原樣。檔案讀不到、不是這個物件、或帳號／密碼是空的，指令結束碼 `2`。訊息只說缺什麼，不含值。

其他人不可讀：權限位元的 other 必須是 0，且所有者可讀。本機 `chmod 600` 與叢集掛載 `0440` 都符合；`644` 拒絕。叢集掛載用 `defaultMode: 0440`，`fsGroup` 等於行程的群組，卷裡的鍵名是 `judicial.json`，掛到上表的系統路徑。資料庫連線仍是 `ENVCITE_CANON_DSN`。

### 行政規則的取得

指令為 `canon guidance sync <gazette|moenv> [--since YYYY-MM-DD] [--rescan]`，實作在 [`law/guidance_sync/`](src/canon/law/guidance_sync/)。每項工作受 jobkit 重試、斷路器與單項隔離保護（預設見 [`law/guidance_sync/run.py`](src/canon/law/guidance_sync/run.py) 的 `POLICY`），每件公文一個交易。

| 來源 | 取得方式 | 出處標示 | 錨定規則 |
|------|----------|----------|----------|
| `gazette` | 行政院公報開放資料 ZIP（只取 `Doc_Style_LName` 為「行政規則」） | 政府資料開放授權條款－第 1 版 | 依內文緊接「核釋」之「法名第幾條」，或「依／依據／為執行」且同句寫明訂定本規則者（含並列條號） |
| `moenv` | 環境部主管法規查詢系統函釋清單與詳細頁（逐頁循序、間隔 ≥ 0.5 秒） | CC BY 4.0 | 只取「相關法規」欄（並列條號各成一處） |

1. **增量與不變性**：未給 `--since` 時公報自上月一日起、函釋走全量；`--rescan` 全部重抓。以（發文機關，正規化字號）識別同一公文，內容不同時不改寫並記 `content_changed`。
2. **錨定（[`law/guidance_anchor.py`](src/canon/law/guidance_anchor.py)）**：法名查視圖 `guidance.law_name`（含沿革舊名；同名多部則 `pcode` 留空並記 `law_ambiguous`），錨定到該條文最細單元在**發文日**現行的文字。
3. **停止適用（`guidance.cessation`）**：
   - **公報廢止**：主旨「廢止／停止適用「甲」」者，自生效日（未寫則為發文日）停止此前刊登「甲」之件（`cause = successor`），每次同步重算全量公報件。
   - **函釋下架**：全量走訪若達完整標準（`crawl_complete`），經 `guidance.settle_crawl` 轉移；連續兩次完整走訪皆未出現者記 `source.item.dropped_on` 並停止適用（`cause = taken_down`）。
   - 約束由 DB 強制：停止日不得早於本件與後函發文日。
4. **匯入問題與結束碼**：warning 記問題不阻擋寫入（如 `law_unresolved`、`provision_unresolved`、`reappeared` 等）；error（`kind_ambiguous`、`incomplete`、`rejected`）不寫入該件。結束碼沿用 jobkit（`0`、`75`、`2`、`1`；隔離超過 1% 時為 `1`）。

### 違憲失效

大法官解釋與憲法法庭判決宣告條文失效（能力問題 A5），記於 `corpus.constitutional_ruling` 與 `corpus.ruling_invalidation`，不放進 `lifecycle_event`。

1. **取得與載入**：
   - **自動取得（`canon ruling fetch`）**：爬憲法法庭網站主文宣告失效之裁判（[`law/cons.py`](src/canon/law/cons.py)，**須標示出處**；已掃描頁面雜湊記於 `corpus.ruling_scan`，`--rescan` 重抓）。由 [`law/ruling_extract.py`](src/canon/law/ruling_extract.py) 保守提出明確失效條文與日期；規則更新遞增 `EXTRACTOR_VERSION` 即可重算，不必重抓。結束碼 `0`／`1`（暫時性）／`2`（頁面或內容不符）。
   - **人工載入（`canon ruling load <file.json>`）**：格式見 [`law/ruling.py`](src/canon/law/ruling.py)，用於多條並列、回指、命令函釋或附條件失效日等保守規則不處理之情形。抽取流程不動人載入的列。
2. **讀取與推論**：
   - 介面為 `CanonService.ListInvalidations` 與 SQL `corpus.invalidations_at`。
   - `annotation.statement_versions_at` 與 `threshold_source_at` 排除失效版本；被排除陳述由 `annotation.invalidated_statements_at` 回報至 `Outcome.invalidated` 與 `ListStatementsResponse.invalidated`，限值失效附 `PROBLEM_KIND_INVALIDATED_DELEGATION`。
   - 部分宣告失效（`partial`）在推論上仍整條排除；僅影響裁判日已施行文字，不影響裁判後修正的新文字。

## 標註與規範

### 標註管線

1. **執行入口**：
   ```bash
   uv run --env-file .env canon pipeline --law <pcode> --models models.toml            # 指定法規
   uv run --env-file .env canon pipeline --pending --limit <N> --models models.toml     # 待辦清單（最多 N 部）
   uv run --env-file .env canon pipeline --law <pcode> --models models.toml --reextract 3  # 最多重抽 3 輪
   ```
   `--limit` 控制單次模型費用上限。驗證與推論規範見 [`AGENTS.md`](AGENTS.md)，呼叫端用法見 [`proto/README.md`](../../proto/README.md)〈標註與推論〉。
2. **重抽機制（`--reextract N`，0–3）**：由視圖 `annotation.reextraction_due` 依資料推得待重抽單元（首次抽取完成、輪數未盡，且有 `unresolved_duty` 或 `duty_gap`），帶審查意見重抽並每輪重新接受；輪數上限由 `extraction_attempt.round` 的 CHECK 強制。
3. **主體與上位條件（確定性程式：`verification/subject.py`、`verification/carry.py`）**：
   - **主體正規化**：義務與禁止陳述的主體須為單元與上層原文拼出的行為人，否則記為 `statement_slot.unstated`（與其他值互斥，由 CHECK 與觸發器強制；契約不暴露此狀態）。
   - **上位條件補齊**：引文前同句或上位導言之條件子句，若未被涵蓋則補為 `statement_condition.carried`（只能為 scope，代碼 `ctx_` 加原文雜湊）。
   - **推理隔離**：推論僅讀罰則陳述的主體槽位（`relation_facts.py` 的 `_SUBJECTS`、`rules.lp` 的 `subj/2`），義務的 `unstated` 主體只影響引用忠實，不改變候選罰則（`tests/test_unstated_subject_neutral.py` 守門）。
4. **唯讀量測工具（`tools/`）**：

   | 工具 | 用途 |
   |------|------|
   | `duty_report.py <dsn> <版本>…` | 統計義務與罰則確認數、關鍵義務與駁回失敗型態 |
   | `duty_sample.py <dsn> <版本>` | 依種子抽樣印出已確認義務、原文、槽位與要件供人工核對 |
   | `duty_defects.py <dsn> <版本>…` | 量測主體與上位條件規則的攔截數 |
   | `duty_audit.py` | 由雙判定者量測語意主體與適用範圍瑕疵 |
   | `duty_effect.py collect\|judge\|report\|bearer` | 盲測修復前後效果（判定者設定 `eval/datasets/duty-fix-effect-judges.toml`）與未明示義務實際負擔者 |
   | `duty_cost.py cost\|unstated` | 統計重抽嘗試數、驗證列數、重複確認浪費與未明示義務罰則鏈 |

### 模型配置（`models.toml`）

端點、角色、嵌入模型、相關性判定者與問答的案件事實抽取模型（`[fact_extractor]`，指名一個 `[model.*]`；沒有這一段就沒有 `ExtractFacts`）集中於 [`models.toml`](models.toml)（標註管線以 `--models` 指定，伺服器與同步由 `ENVCITE_CANON_MODELS` 指定）。金鑰不入檔，由端點名推導（`ENVCITE_<端點名大寫>_API_KEY`），依序自環境變數與系統鑰匙圈解析，規則見 [`CONFIGURATION.md`](../../CONFIGURATION.md)。

```bash
uv run --env-file .env canon pipeline --models models.toml --check   # 探測模型設定，不連資料庫
```

- `[embedding]`：只需該端點金鑰；缺標註管線端點金鑰不影響伺服器啟動。
- `[reranker]`：查詢精排模型（`/rerank` 路由）；未設定則略過精排。深度由 `ENVCITE_CANON_RERANK_CANDIDATES` 設定，降級行為見 [`proto/README.md`](../../proto/README.md) 與 `AGENTS.md`。
- `[relevance]`：黃金測試集判定者設定（`judges` 至少兩個不同家族；`system_families` 列受測路徑家族，判定者不得屬於其中）。

### 規範關係：授權

命令依哪一部法律的哪一條訂定（行政程序法 §150 II），以 `assertion_kind = delegation` 存於 `annotation.delegation`。

1. **抽取**：`canon sync` 匯入後由 [`law/delegation.py`](src/canon/law/delegation.py) 從命令首條純規則抽取（`derivation.method = rule`），已有主張之引文不重寫。
2. **雙端錨定**：法律端僅對語料內唯一法規標題（對不到、重名或無該條文則 `law_resource_id`／`law_provision_id` 留空）；命令端錨定 `(法規, 條文單元, 內容雜湊)`，依查詢日動態解析，不鎖死目標版本。
3. **讀取與推論**：
   - SQL 函式：`annotation.delegations_at`（命令授權依據）與 `annotation.delegated_by`（法律授權命令），皆須已確認或規則推導方回傳。
   - 推論：[`reasoning/facts/chains.py`](src/canon/reasoning/facts/chains.py) 沿 `delegations_at` 往上追溯至法律；授權條文違憲失效或鏈斷裂時，命令罰則與處罰命令之罰則附條件，義務結論不變。

### 規範關係：罰則的連結

罰則陳述以非空外鍵 `annotation.sanction.punished_statement_id` 指向單一義務或禁止陳述（表與規則見 [`migrations/0001_baseline.sql`](migrations/0001_baseline.sql) 的 `annotation.sanction`，寫入在 [`verification/link.py`](src/canon/verification/link.py)）。

1. **連結約束**：寫入罰則時受罰陳述必須為 `confirmed`（觸發器強制）；受罰陳述被駁回時罰則一併駁回。
2. **待連結罰則**：`annotate.load` 時暫連不上的罰則連同相依條件存為 `annotation.held_penalty`；`canon pipeline` 於第一次接受後嘗試連結（記於 `held_penalty_link`）並再接受一次。
3. **依賴圖與讀取**：
   - `canon sync` 匯入後由 [`violation.py`](src/canon/violation.py) 寫入 `annotation.violation`（現行視圖 `annotation.current_violation`），區分 `duty`（直接處罰條）與 `delegated_rule`（授權命令條）；`retrieval.sanction_chain` 沿它往下走到處罰的處罰（最多 3 層），混合檢索與函釋入口命中義務條時都用它展開。
   - 讀取介面：`annotation.penalty_candidates`（候選義務）、`annotation.held_penalty_state`（`linked`／`linkable`／`awaiting_duty`／`no_duty`／`no_citation`）、`annotation.duty_gap`（被引為違反但缺未駁回義務之條文）。
4. **依查詢日解析所罰的義務**：推論與 `ListStatements` 經 `annotation.sanctions_at`（[`migrations/0005_sanctions_at.sql`](migrations/0005_sanctions_at.sql)）讀罰則，罰則條文所指的是義務條文在該日的規定（中央法規標準法 §17）。連結寫入後不改；受罰陳述該日不在有效陳述之內、其條文單元的文字已不同，且舊陳述是其文字裡唯一的義務、該日單元也恰有一個義務時，所罰改指該日的義務；其餘情形為原連結。修正前的日子仍是原連結，`Evaluate` 的行為時與裁處時各連到當時的義務。

## 評估

### 黃金測試集

檢索的召回與拒答以外部黃金題庫（`golden.yaml`，不入庫）或倉庫內原生題庫量測，指標與顯著性檢定由 [ranx](https://github.com/AmenRa/ranx) 計算（工具在 `tools/`）。

#### 目錄

`eval/` 輸入與產出分開放，工具以 `--out` 指到 `results/`，不寫回 `datasets/`（建題與匯回 qrels 除外）：

| 目錄 | 內容 | 規則 |
|------|------|------|
| `eval/datasets/` | 題庫、qrels、切分、案例庫、判定者設定 | 工具的輸入；改動即新版本，檔名帶建立日期（現行 qrels 與切分除外） |
| `eval/results/` | 報告（`.json`＋`.md`）、各臂 `.run.json`、判定與校準檔、`.notes.md` | 工具的產出，檔名帶日期；報告記錄同目錄的 run，所以報告與其 run 放在一起；論文以此目錄的路徑引用 |

#### 指令

**標準評估流程（步驟 1–7）**：

| 步驟 | 指令（在本目錄） | 產出與前置條件 |
|------|------------------|----------------|
| 1. 建 qrels | `uv run python tools/golden_retrieval_eval.py qrels <golden.yaml> --out eval/datasets/golden-qrels.json` | 初始 qrels（需資料庫） |
| 2. 切分題集 | `uv run python tools/golden_set.py split <golden.yaml> --out eval/datasets/golden-split.json` | `dev`／`test` 切分檔 |
| 3. 量測檢索 | `uv run python tools/golden_retrieval_eval.py measure <golden.yaml> --qrels … --split … --subset dev --out eval/results/golden-retrieval-<日期>` | `.json`／`.md` 報告與各臂 `.run.json`；加 `--rerank [--rerank-depths 10,20,30,50]` 另測精排臂與延遲分布 |
| 4. 顯著性比較 | `uv run python tools/golden_retrieval_eval.py compare --qrels … <a>.run.json <b>.run.json [--out …]` | 各臂指標、Hole@10 與 Fisher 隨機化檢定 p 值 |
| 5. 校準判定器 | `uv run python tools/relevance_judge.py calibrate <golden.yaml> --qrels … --split … --cache <倉庫外>/judge-cache.jsonl --out eval/results/golden-calibration-<日期>.json` | 開發集正／負例表現與 κ（只依此調整判定提示） |
| 6. 補判空洞 | `uv run python tools/relevance_judge.py pool <golden.yaml> --qrels … --split … --subset dev --run <a>.run.json --calibration … --cache … --out eval/results/golden-judgements-<子集>-<日期>.json` | 封存判定檔（須與校準檔同提示雜湊；支援 `--as-of`、`--workers` 與快取續跑） |
| 7. 匯回 qrels | `uv run python tools/golden_set.py judge --qrels eval/datasets/golden-qrels.json --judgements eval/results/golden-judgements-….json --out eval/datasets/golden-qrels.json` | 合併判定後的新版 qrels |

**專項評估工具**：

| 工具 | 指令重點 | 用途 |
|------|----------|------|
| 選下界 | `tools/answerability_floor.py`、`tools/floor_risk_coverage.py`（`--lexical`／`--semantic`） | 印出開發集分數分布供訂下界，不修改設定 |
| 融合消融 | `tools/ablation_fusion_eval.py arms …`（至少 3 輪）→ `grid … [--export-runs <目錄>]` | 離線網格搜尋 `(候選池 P, 名次常數 k)` 並做 Holm 校正檢定 |
| 下界落差 | `tools/threshold_calibration.py --eval … [--arm hybrid] --out …` | 依共用 `relevance-floors.json` 隔離前 k 條與前 10 條通過下界之落差 |
| 關鍵字題庫 | `tools/keyword_golden.py build` → `verify` → `measure <題庫> [--splits dev]` | 以程式抽取之定義條與無答案詞量測已知項目搜尋與誤放 |
| 函釋入口回歸 | `tools/guidance_regression.py build` → `verify` → `measure <題庫>` → `compare <前> <後>` | 用函釋自己的詞問，查預期條文（錨定條文加本體論罰則鏈）是否都在名單裡；題目只由規則從資料產生，退步的題讓 `compare` 以 1 結束 |
| 端到端與彙總 | `tools/grounding_check_eval.py` → `tools/natural_answer_eval.py --input …` | 量測實際拒答與引用表現，並重新彙總拒答分類 |
| 事實抽取 | `tools/fact_extraction_eval.py --out eval/results/fact-extraction-<日期> [--candidates K] [--extractor <模型>] [--seed N] [--split dev\|test]`（需資料庫、嵌入與模型端點） | 量模型能否把問句裡明說的事實對到概念代碼、不憑空填值：標準答案由已確認陳述的要件結構產生，問句由另一家族的模型改寫，候選概念依問句由嵌入檢索（不依標準答案挑），另一家族的模型事後稽核，全程無人工標註；資料庫綱要落後於程式時只量抽取，不跑推理機 |
| 概念沿用 | `tools/concept_reuse_eval.py --out eval/results/concept-reuse-<日期> [--units N] [--seed N]`（需資料庫、嵌入與模型端點） | 量抽取者宣告概念前先看到既有概念，是否少產生意思重複的新概念：取已有標註的法規裡還沒有陳述的單元，用不給與給既有概念兩種提示各抽一次（不寫入），宣告的概念分沿用、意思重複（另一個家族的模型判斷）與真正新的；全程無人工標註 |
| 概念重複 | `tools/concept_twins.py --out eval/results/concept-twins-<日期>`（需資料庫、嵌入與模型端點） | 量方案裡「意思相同、名稱不同」的既有概念有多少、牽動多少陳述：被陳述用到的概念各取名稱最相近的幾個，兩位不同家族的模型都判為同一個法律上的事實或要件才算；只量、不改資料庫 |
| 效能基準 | `tools/load_bench.py run <golden.yaml> --split … (--pg-container … \| --skip-cold)` | 以 [k6](https://grafana.com/docs/k6/) 量測 `search`／`ground`／`evaluate` 延遲與吞吐階梯 |

#### 原生題庫

不依賴外部 `golden.yaml`：由 [`tools/native_golden.py`](tools/native_golden.py) 依語料結構程式化建題（與資料庫快照及 `--as-of` 決定性對應），題庫本體置於 `eval/datasets/native-golden-<日期>.json`。修改產生規則須遞增 `GENERATOR_VERSION` 並重建；不可為讓 `verify` 乾淨而放寬文法。

| 題型（`kind`） | 桶 | 查詢來源 | 標準答案 |
|------|------|------|------|
| `definition_term`、`definition_question` | `exact_id` | 法規定義詞彙或「X 的定義是什麼」 | 全部現行條文中定義該詞之條 |
| `duty_penalty` | `multi_hop` | 違反某法第 N 條之處罰 | 寫明「違反第 N 條規定者，處…」之罰則條 |
| `delegation` | `multi_hop` | 某命令之授權母法條 | 命令第一條所載母法授權條 |
| `applies_mutatis_mutandis` | `multi_hop` | 某條準用哪一條 | 該條所寫被準用條 |
| `absent_term`、`absent_question` | `negative` | 候選詞 | 無（全部現行條文皆不含） |

1. **建題**：`uv run --env-file .env python tools/native_golden.py build --as-of <日期> --out eval/datasets/native-golden-<日期>.json`（只需語料 DB，不需嵌入）。
2. **查證**：`uv run --env-file .env python tools/native_golden.py verify eval/datasets/native-golden-<日期>.json`（重建比對、證據句核對與獨立掃描）。
3. **衍生**：`uv run python tools/native_golden.py derive eval/datasets/native-golden-<日期>.json`（輸出 `.qrels.json` 與 `.split.json` 供標準流程量測）。

#### 格式

所有 JSON 評估檔皆含 `sha256`（移除該欄後之正規化 UTF-8 JSON 雜湊），`golden_set.read` 驗算不符即停。

- **條文代號**：中央法規為 `<pcode>-art-<條號>`；地方法規帶管轄前綴 `<管轄>/<在地識別碼>-art-<條號>`（管轄一律取自資料庫列）。
- **核心資料檔**：
  - **`qrels`**：題號 → 條文代號 → `0–3` 分（≥ `relevant_from` 為相關；無答案題不入列）。記 `golden_sha256`、`snapshots`（快照不符即停）、`upstream`（只能往上調）、`graded_by` 與併入紀錄。
  - **切分（`split`）**：依桶分層、固定種子切為 `dev`（調參與選下界）與 `test`（定案後報告）。
  - **`run` 與報告**：`run` 記前 k 條排行與快照雜湊，不綁 qrels；報告記 `hit_rate@k`、`mrr`、`ndcg@10`、`hole@10`（前 10 條未判定比例）。`compare` 僅能比較同子集同批題目，p ≥ α 不稱為改善或退步。
- **相關性判定機制**：
  - **判定者**：讀 `models.toml` 的 `[relevance]`，至少兩位不同家族且排除 `system_families`；執行前以 `SameModel` 探測別名。
  - **判定方式**：繁體中文 UMBRELA zero-shot 提示（0–3 分），每位判定一次取較低分，任一失敗即為洞。
  - **校準與判定檔**：`calibration` 僅在 `dev` 驗證正負例表現與一致性；`judgements` 記完整雜湊、提示、`agreement`（Cohen's κ 等）與未命中快取之用量，快取放倉庫外。

### 涵攝評估

以真實條文規範陳述（水污染防治法第 7 條與第 40 條）為大前提、案件事實為小前提，呼叫 clingo 量測三值 Kleene 涵攝、缺漏事實識別、裁量判斷、責任主體分流與行政罰法總則阻卻之正確性（能力問題 C3、C5、C6、C7）。

- **案例庫**：[`eval/datasets/subsumption_benchmark.json`](eval/datasets/subsumption_benchmark.json)
- **評估工具**：[`tools/subsumption_eval.py`](tools/subsumption_eval.py)（輸出 JSON 與 Markdown 報告）
- **守門測試**：[`tests/test_subsumption_eval.py`](tests/test_subsumption_eval.py)

### 能力問題登記

canon 必須回答的問題與實作現況唯一登記在 [`competency.toml`](competency.toml)（`status` 為 `answerable`、`partial` 或 `design_only`），證據分為 `db`、`rpc`、`field` 與 `test`。[`tests/test_competency.py`](tests/test_competency.py) 驗證題號覆蓋與證據存在性；新增或更新現況時，登記檔與該測試須同次更新。
