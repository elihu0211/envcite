# env-law API

gRPC 契約，`buf` 單一模組（根為本目錄）。欄位與方法的語意寫在 `.proto` 的註解裡，本檔只寫註解放不下的：約定與呼叫端的接法。

| 套件 | 服務 | 租戶 | 對象 |
|------|------|------|------|
| `env_law.ontology.v1` | `OntologyService` | 共享 | 內部 |
| `env_law.consult.v2` | `ConsultationService` | 隔離 | 內部 |

外部用戶端只經 `platform` 的 REST：契約是 [`services/platform/openapi/openapi_v2.json`](../services/platform/openapi/openapi_v2.json)。

```bash
buf lint
buf breaking --against '.git#branch=main'
```

產生碼與破壞性變更的規則見倉庫根 `AGENTS.md` 的 Contract。

## 約定

**Metadata**

| key | 必填 | 說明 |
|-----|------|------|
| `authorization` | 是 | `Bearer <內部 token>`，缺少或不符 → `UNAUTHENTICATED` |
| `x-tenant-id` | consult 必填 | 配額與稽核；ontology 只用於日誌。consult 缺少 → `INVALID_ARGUMENT` |
| `x-request-id` | 否 | 只用於日誌 |

**`as_of`**：ISO 8601 日期，依哪一天施行的條文；空字串為現行。沒有任何版本涵蓋該日時回**查無**，不回現行條文。

**狀態碼**

| 狀態碼 | 何時 | 重試 |
|--------|------|------|
| `INVALID_ARGUMENT` | 參數不合法、缺 `x-tenant-id` | 否 |
| `UNAUTHENTICATED` | 內部 token 缺少或錯誤 | 否 |
| `NOT_FOUND` | 找不到法規，或沒有版本涵蓋 `as_of` | 否 |
| `UNAVAILABLE`、`DEADLINE_EXCEEDED` | 下游不可用或逾時 | 是 |

consult 呼叫 ontology 時，ontology 的狀態碼原樣轉出。

## 呼叫端的語言模型

**本系統不執行生成式模型。** 它決定可以引用什麼、能不能回答，並交出證據；寫成散文的是呼叫端自己的模型。

要作答，呼叫 **`Ground`**，不要自己拿 `SearchArticles` 的結果給模型：檢索永遠回最佳的幾筆，即使語料裡毫無相關內容，「有沒有答案」的判斷由 consult 做掉。

- `question` 傳使用者原句，不要先改寫；`SearchArticles` 的 `query` 同樣傳原文，斷詞由服務做。
- 回應的 `outcome` 恰有一個：`evidence`（1 至 8 筆引用，依相關程度排序）或 `refused`（拒答）。拒答是結果，不是錯誤：呈現給使用者，不重試、不呼叫模型。
- 交給模型時，證據以代號（`L1`…）開頭，限定模型只能以 `[L1]` 引用。
- 模型輸出要核驗：代號集合是封閉的，比對字串即可，剝除不在集合內的代號；剝除後沒有任何引用，視同拒答。
- 以 `Citation.iri`（ELI IRI）呈現出處；代號只在單次回應內有意義，不要儲存。
