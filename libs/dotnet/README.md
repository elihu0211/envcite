<!--
type: reference
applies-to: all
owns: [dotnet-service-defaults]
-->

# libs/dotnet

.NET 服務（[platform](../../services/platform/README.md)、[consult](../../services/consult/README.md)）共用的專案，以 ProjectReference 引用，不發佈。SDK、套件版本、建置屬性與程式碼風格在倉庫根（`global.json`、`Directory.*.props`、`.editorconfig`）。回[倉庫總覽](../../README.md)。

## EnvCite.ServiceDefaults

| 型別 | 做什麼 |
|------|--------|
| `ISectionOptions`、`AddSectionOptions` | 設定型別自己宣告區段名稱；繫結並在主機啟動時驗證，設定錯了 pod 起不來 |
| `InternalGrpcClientOptions`、`AddInternalGrpcClient` | 內部 gRPC 用戶端：`Address`（叢集內 `dns:///<headless service>:<port>`）、`Timeout`；round_robin、只重試 `UNAVAILABLE` 並節流、keepalive、DNS 定期重新解析、RED 指標 |
| `GrpcClientMetrics`、`RpcMetrics` | `rpc.client.call.duration`；伺服器端的 `rpc.server.call.duration` 用同一組邊界與標籤（`rpc.system.name`、`rpc.service`、`rpc.method`、`rpc.response.status_code`） |
| `AddServiceTelemetry` | OpenTelemetry 的追蹤、指標與記錄：只認標準 `OTEL_*` 鍵，沒設 `OTEL_EXPORTER_OTLP_ENDPOINT` 就不匯出；探針（HTTP `/health`、gRPC 健康檢查）不進追蹤 |

連線選項與 Python 伺服器成套：用戶端的 keepalive 間隔必須大於伺服器容許的最短 ping 間隔，數值與不等式見 [`envcite-grpc`](../python/envcite-grpc/README.md)，改一邊要看另一邊。
