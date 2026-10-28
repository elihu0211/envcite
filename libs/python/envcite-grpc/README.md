---
type: reference
applies-to: all
owns: [grpc-connection-options]
---

# envcite-grpc

兩個以上服務共用的 gRPC 管線零件：連線選項（`connection.py`）、期限換算（`deadline.py`）、RED 指標（`metrics.py`）、遙測與優雅關閉。

## 連線選項

選項成套：伺服器允許的最短 ping 間隔必須小於客戶端的 keepalive 間隔，否則伺服器以 `too_many_pings` 斷線；測試守著這個不等式。客戶端不只 Python：platform 的 C# 用戶端（`services/platform` 的 `InternalGrpcClient`）也依同一組值，改一邊要看另一邊。
