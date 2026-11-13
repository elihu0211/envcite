<!--
type: reference
applies-to: all
owns: [consult-surfaces, local-demand-metric]
-->

# EnvCite.Consult

接地服務：以本體服務供給的內容，為呼叫端的語言模型備妥可引用的封閉證據集（代號如 `L1`、`L2`，附條文），或拒答。回[倉庫總覽](../../README.md)。

- 介面：gRPC `ConsultationService.Ground`，契約 [`consult.proto`](../../proto/envcite/consult/v2/consult.proto)；呼叫端怎麼用證據集、怎麼核驗模型的輸出，見 [`proto/README.md`](../../proto/README.md)。
- 呼叫端 [`platform`](../platform/README.md)（對外為 `POST /v2/ground`）；上游 [`ontology`](../ontology/README.md)。
- 租戶隔離，`x-tenant-id` 必填；本服務自己不存資料。
- 證據集的來源語料只涵蓋中央法規，地方自治法規不在範圍內；範圍與拒答的解讀見 [`platform` 的「語料範圍」](../platform/README.md#語料範圍)。

## 概念

| 組 | 節 | 內容 |
|----|----|------|
| 服務 | [入口](#入口) | gRPC 伺服器 |
| 服務 | [設定](#設定) | 本體位址、呼叫期限、相關性下界 |
| 服務 | [健康檢查](#健康檢查) | 收到 SIGTERM 後回 `NOT_SERVING` |
| 指標 | [地方自治法規的需求計數](#地方自治法規的需求計數) | 以真實提問累積地方法規的需求 |

## 服務

### 入口

| 入口 | 型態 | 做什麼 |
|------|------|--------|
| `EnvCite.Consult.dll` | 長駐 | gRPC 伺服器（明文 h2c）；映像的預設 entrypoint |

### 設定

設定鍵以 `src/EnvCite.Consult/appsettings.json` 為準，環境變數以 `__` 分隔區段（如 `ENVCITE_Ontology__Address`）。

| 鍵 | 說明 |
|----|------|
| `Ontology:Address` | 本體的 gRPC 位址；叢集內為 `dns:///<headless service>:<port>`，本機為 `http://localhost:<port>`。格式錯誤時啟動即失敗 |
| `Ontology:Timeout` | 呼叫本體的上限；實際期限是它與呼叫端剩下的期限取小者 |
| `Relevance:LexicalFloor`、`Relevance:SemanticFloor` | 相關性下界：一條檢索結果字面分數或語意相似度任一達標才進證據集；缺鍵或超出範圍時啟動即失敗。字面高信心線固定為下界的 1.5 倍 |
| `Relevance:HighConfidenceSemantic` | 語意臂的高信心線；須高於語意下界、不超過 1 |
| `ASPNETCORE_HTTP_PORTS` | 監聽的埠；映像內固定為 `50052` |
| `OTEL_*` | 遙測，約定見 [`libs/dotnet`](../../libs/dotnet/README.md) |

### 健康檢查

gRPC 健康檢查協定（`grpc.health.v1.Health`）：空字串與 `envcite.consult.v2.ConsultationService` 平時都是 `SERVING`。收到 SIGTERM 後兩者都回 `NOT_SERVING`，排空數秒後才停止接受連線。不查本體的狀態。

## 指標

### 地方自治法規的需求計數

語料不含地方自治法規（[語料範圍](../platform/README.md#語料範圍)）；是否擴充取決於真實的提問，由本計數在上線後累積。

`envcite.consult.ground.outcomes` 是計數器：每次 `Ground` 得出結果就加一，證據與拒答都計，所以能算比率。沒有結果的呼叫（缺租戶、參數錯誤、本體不可用）不計，它們看 RED 指標（見 [`proto/README.md`](../../proto/README.md)）。

| 標籤 | 值 | 意義 |
|------|----|------|
| `outcome` | `grounded` | 回了證據 |
| | `no_evidence`、`out_of_scope`、`missing_input`、`local_law_uncovered` | 拒答，對應 `Refused.Reason` |
| `local_marker` | `none` | 問句沒有地方標記 |
| | `jurisdiction` | 問句點名了縣市（全名，「臺」「台」皆認） |
| | `ordinance` | 問句出現「自治條例」這類自治法規字樣；與縣市並存時取這一類 |

標籤只有這兩個、值域固定；租戶、問句與命中的詞不進指標。詞表與判定規則的真值在 `LocalMarker.cs`。標記不影響檢索與接地，對回應的影響只有一處：沒有達標條文、又是 `ordinance` 時，拒答原因是 `local_law_uncovered`，不是 `no_evidence` 或 `out_of_scope`；有達標條文就照常回證據，因為中央條文可能正是答案。

**怎麼讀**：看 `local_marker` 不是 `none` 的結果。

- `outcome` 為 `no_evidence`、`out_of_scope` 或 `local_law_uncovered` 的次數，除以全部結果（所有標籤加總），是「問了地方，語料沒有」占總提問的比率；除以 `local_marker` 不是 `none` 的結果，是地方提問中落空的比率。
- `ordinance` 是明白在問地方規定，語料沒有，不論 `outcome` 都沒拿到地方規定；`jurisdiction` 可能只是提問者說明所在地，是較弱的訊號。

開發指令與實作規範見 [`AGENTS.md`](AGENTS.md)。
