# env-law API

本目錄的 `.proto` 是 gRPC API 的唯一真值；各欄位、各方法的精確語意寫在 `.proto` 的註解裡，本檔不重述。本檔寫的是註解放不下的內容：服務之間怎麼組合、呼叫端該怎麼接，特別是**語言模型接在哪裡**。

| 套件 | 服務 | 租戶性質 | 狀態 |
|------|------|----------|------|
| `env_law.ontology.v1` | `OntologyService` | 共享，不分租戶 | 使用中 |
| `env_law.consult.v2` | `ConsultationService` | 租戶隔離 | 使用中 |

兩者都是**內部服務**。外部用戶端一律經 `platform` 的 REST 呼叫（見文末〈對外 REST〉）。

## 語言模型不在本系統之內

本系統不執行任何生成式語言模型。它負責決定**可以引用什麼**、**能不能回答**，並把決定連同證據一起交出去；把證據寫成散文的是**呼叫端自己的模型**。

```
 本系統                                        呼叫端
 ──────────────────────────────────────────   ───────────────────────────────
 retrieve  本體檢索（字面）
 ground    程式指派封閉引用集合 L1、L2…
 assess    規則決定能不能答 ── 拒答 ──────────▶ 直接呈現拒答，不呼叫模型
                  │
                  └── 證據 ───────────────────▶ generate  自己的模型寫散文，只能以代號引用
                                                verify    剝除不在集合內的代號
```

因此：

- 不必選模型供應商，也不必保管模型的金鑰；模型與提示詞隨呼叫端的節奏更換。
- **引用可核驗性由呼叫端履行。** 本系統保證交出的集合是封閉、有序、附條文的；呼叫端要保證散文只引用這個集合，做法見下文〈呼叫端的語言模型〉。

## 共通約定

**Metadata**

| key | 必填 | 說明 |
|-----|------|------|
| `authorization` | 是 | `Bearer <內部 token>`。缺少或不符 → `UNAUTHENTICATED` |
| `x-tenant-id` | consult 必填；ontology 選填 | consult 以它決定配額與稽核軌跡，缺少 → `INVALID_ARGUMENT`。ontology 只用於日誌 |
| `x-request-id` | 否 | 只用於日誌 |

**`as_of`**：ISO 8601 日期（`YYYY-MM-DD`），指定「依哪一天施行的條文」。空字串表示現行。語料中沒有任何版本涵蓋該日時，回傳的是**查無**，絕不是現行條文——兩者對讀者無從分辨，錯的那個會被當成對的。

**狀態碼**

| 狀態碼 | 何時 | 可重試 |
|--------|------|--------|
| `INVALID_ARGUMENT` | 參數不合法（`as_of` 不是日期、`limit` 超出範圍、查詢斷詞後為空）、缺 `x-tenant-id` | 否 |
| `UNAUTHENTICATED` | 內部 token 缺少或錯誤 | 否 |
| `NOT_FOUND` | `GetLaw`／`ListArticles` 找不到法規，或沒有版本涵蓋 `as_of` | 否 |
| `UNAVAILABLE`、`DEADLINE_EXCEEDED` | 下游不可用或逾時 | 是 |

consult 呼叫 ontology 時，ontology 的狀態碼**原樣轉出**：無效的 `as_of` 在 consult 端仍是 `INVALID_ARGUMENT`，ontology 不可用仍是 `UNAVAILABLE`。

## OntologyService（`env_law.ontology.v1`）

| 方法 | 用途 |
|------|------|
| `GetLaw(id, as_of)` | 取一部法規（ELI `LegalResource`）；標題取自該日施行的版本 |
| `ListArticles(law_id, as_of)` | 該日施行版本的各條，依條次排序 |
| `SearchArticles(query, as_of, limit)` | 字面檢索，最佳者在前。`limit` 為 0 取預設 10，上限 50 |

`SearchArticles` 的 `query` 傳**原文**：斷詞由服務以建立索引時的同一個分詞器執行，呼叫端自行斷詞只會安靜地少命中。

檢索總會回傳最佳的幾筆，即使語料中毫無相關內容；要判斷「這裡有沒有答案」，須以 `ArticleHit.lexical_score` 對照校定過的下界。這正是 consult 替呼叫端做掉的事，所以要作答的呼叫端應呼叫 consult，而不是自己拿 `SearchArticles` 的結果給模型。

## ConsultationService（`env_law.consult.v2`）

一個方法：`Ground(question, as_of) → GroundResponse`。一元呼叫、只讀，可安全重試。

- `question` 傳使用者的**原句**，不要先讓模型改寫：檢索是依人怎麼問來調校的，改寫後就是另一個查詢。
- 回應的 `outcome` 恰有一個：

| `outcome` | 意義 | 呼叫端該做的事 |
|-----------|------|----------------|
| `evidence` | 1 至 8 筆 `Citation`（代號 `L1`…、ELI IRI、標籤、條文全文），依相關程度排序；附 `confidence` | 交給自己的模型作答 |
| `refused` | 拒答，附原因與說明 | 呈現給使用者；**不重試、不呼叫模型、不計為失敗** |

**拒答原因**

| `Refused.Reason` | 意義 |
|------------------|------|
| `REASON_MISSING_INPUT` | 問句為空（在檢索前就判定） |
| `REASON_NO_EVIDENCE` | 什麼都沒檢索到：語料缺漏，或 `as_of` 當時法規尚未施行 |
| `REASON_OUT_OF_SCOPE` | 有檢索到，但沒有一筆達相關性下界：問題談的是別的事 |

**信心度**：`CONFIDENCE_HIGH` 表示至少一筆依據是直接的；`CONFIDENCE_LOW` 仍有證據、可以作答，但應向使用者表明依據間接。

## 呼叫端的語言模型

本系統不提供提示詞模板——那是呼叫端的產品決定。下列是契約層面的要求，與用哪一家模型無關。

**1. 把證據原樣交給模型，並限定引用方式。** 每筆證據以代號開頭，模型只能以 `[L1]` 這種形式引用。例如：

```text
僅依下列條文回答。引用時只能寫方括號中的代號，例如 [L1]；條文未涵蓋的，說明無法依現有條文回答。

[L1] 噪音管制法 第 9 條
工廠不得發出超過管制標準之噪音。

問題：工廠噪音超過管制標準會怎樣？
```

**2. 核驗模型的輸出。** 集合是封閉的，核驗只需比對字串，不必再呼叫本系統：

```python
import re

from env_law.consult.v2 import consult_pb2

CODE = re.compile(r"\[(L\d+)\]")


def verify(answer: str, evidence: consult_pb2.Evidence) -> tuple[str, list[str]]:
    """回傳（剝除捏造代號後的答案, 實際引用的代號）。"""
    allowed = {c.code for c in evidence.citations}
    cleaned = CODE.sub(lambda m: m.group(0) if m.group(1) in allowed else "", answer)
    cited = [c.code for c in evidence.citations if f"[{c.code}]" in cleaned]
    return cleaned, cited
```

`cited` 為空時，答案沒有任何依據，應視同拒答處理，而不是照樣呈現。

**3. 以 IRI 呈現出處。** `Citation.iri` 是 ELI IRI，可反查到條文；`label` 供顯示。代號只在單次回應內有意義，不要儲存它來指稱條文。

## 對外 REST（platform）

外部用戶端以 API key（`Authorization: Bearer sk_live_…`）呼叫 `platform`，由它轉成上述 gRPC 呼叫並計量。現有的 `/v1` 端點：

| 方法 | 路徑 | 說明 |
|------|------|------|
| GET | `/v1/laws/{lawId}` | 包裝 `GetLaw`（現行版本） |
| GET | `/v1/laws/{lawId}/articles` | 包裝 `ListArticles`（現行版本） |
| POST | `/v1/ground` | 包裝 consult 的 `Ground` |
| GET | `/v1/me`、`/v1/usage`、`/v1/api-keys` | 租戶、用量、API key 管理 |

完整描述由 ASP.NET Core 產生，開發環境在 `/openapi/v1.json`。`SearchArticles` 不對外開放：要作答的呼叫端應該用 `/v1/ground`，理由見上文。

**`POST /v1/ground`**

```http
POST /v1/ground
Authorization: Bearer sk_live_…
Content-Type: application/json

{"question": "工廠噪音超過管制標準會怎樣？", "asOf": "2024-01-01"}
```

`asOf` 可省略。證據與拒答都回 **200**，以 `outcome` 區分：

```json
{"outcome": "evidence", "confidence": "low",
 "citations": [{"code": "L1", "iri": "https://env.law/eli/tw/law/…/art_9",
                "label": "噪音管制法 第 9 條", "text": "工廠不得發出超過管制標準之噪音。"}]}
```

```json
{"outcome": "refused", "refusal": {"reason": "no_evidence", "detail": "檢索無任何結果"}}
```

`reason` 為 `no_evidence`、`out_of_scope`、`missing_input`，語意同上文的拒答原因表。`asOf` 不合法回 400（Problem Details），consult 不可用或逾時回 503。證據與拒答都計量為一次 `consult.ground`，錯誤不計。
