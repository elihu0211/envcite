---
type: reference
applies-to: all
owns: [consult-surfaces]
---

# envcite-consult

接地服務：以本體服務供給的內容，為呼叫端的語言模型備妥可引用的封閉證據集（代號如 `L1`、`L2`，附條文），或拒答；呼叫端怎麼用見 [`proto/README.md`](../../proto/README.md)。回[倉庫總覽](../../README.md)。

- 介面：gRPC `ConsultationService.Ground`，契約 [`consult.proto`](../../proto/envcite/consult/v2/consult.proto)；呼叫端怎麼用證據集、怎麼核驗模型的輸出，見 [`proto/README.md`](../../proto/README.md)。
- 呼叫端 [`platform`](../platform/README.md)（對外為 `POST /v2/ground`）；上游 [`ontology`](../ontology/README.md)。
- 租戶隔離，`x-tenant-id` 必填；本服務自己不存資料。

## 入口

| 入口 | 型態 | 做什麼 |
|------|------|--------|
| `envcite-consult` | 長駐 | gRPC 伺服器；環境變數以 [`server.py`](src/envcite_consult/server.py) 為準 |

開發指令與實作規範見 [`AGENTS.md`](AGENTS.md)。
