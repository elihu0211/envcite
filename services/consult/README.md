---
type: reference
applies-to: all
owns: [consult-surfaces]
---

# EnvCite.Consult

接地服務：以本體服務供給的內容，為呼叫端的語言模型備妥可引用的封閉證據集（代號如 `L1`、`L2`，附條文），或拒答；呼叫端怎麼用見 [`proto/README.md`](../../proto/README.md)。回[倉庫總覽](../../README.md)。

- 介面：gRPC `ConsultationService.Ground`，契約 [`consult.proto`](../../proto/envcite/consult/v2/consult.proto)；呼叫端怎麼用證據集、怎麼核驗模型的輸出，見 [`proto/README.md`](../../proto/README.md)。
- 呼叫端 [`platform`](../platform/README.md)（對外為 `POST /v2/ground`）；上游 [`ontology`](../ontology/README.md)。
- 租戶隔離，`x-tenant-id` 必填；本服務自己不存資料。
- 證據集的來源語料只涵蓋中央法規，地方自治法規不在範圍內；範圍與拒答的解讀見 [`platform` 的「語料範圍」](../platform/README.md#語料範圍)。

## 入口

| 入口 | 型態 | 做什麼 |
|------|------|--------|
| `EnvCite.Consult.dll` | 長駐 | gRPC 伺服器（明文 h2c）；映像的預設 entrypoint |

## 設定

設定鍵以 `src/EnvCite.Consult/appsettings.json` 為準，環境變數以 `__` 分隔區段（如 `ENVCITE_Ontology__Address`）。

| 鍵 | 說明 |
|----|------|
| `Ontology:Address` | 本體的 gRPC 位址；叢集內為 `dns:///<headless service>:<port>`，本機為 `http://localhost:<port>`。格式錯誤時啟動即失敗 |
| `Ontology:Timeout` | 呼叫本體的上限；實際期限是它與呼叫端剩下的期限取小者 |
| `Relevance:LexicalFloor`、`Relevance:SemanticFloor` | 相關性下界：一條檢索結果字面分數或語意相似度任一達標才進證據集；缺鍵或超出範圍時啟動即失敗。字面高信心線固定為下界的 1.5 倍 |
| `Relevance:HighConfidenceSemantic` | 語意臂的高信心線；須高於語意下界、不超過 1 |
| `ASPNETCORE_HTTP_PORTS` | 監聽的埠；映像內固定為 `50052` |
| `OTEL_*` | 遙測，約定見 [`libs/dotnet`](../../libs/dotnet/README.md) |

## 健康檢查

gRPC 健康檢查協定（`grpc.health.v1.Health`）：空字串與 `envcite.consult.v2.ConsultationService` 平時都是 `SERVING`。收到 SIGTERM 後兩者都回 `NOT_SERVING`，排空數秒後才停止接受連線。不查本體的狀態。

開發指令與實作規範見 [`AGENTS.md`](AGENTS.md)。
