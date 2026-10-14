"""長壽命 gRPC 連線的健康設定，伺服器與客戶端共用，數值須成套調整。

依據是 gRPC 官方的 keepalive 指南（doc/keepalive.md）與 grpc-python 的 keep_alive 範例：

- **連線最長壽命**：連線活滿 5 分鐘（±10% 抖動，官方實作內建）就送 GOAWAY，寬限 30 秒讓進行中
  的呼叫收尾。客戶端重連時重新解析 DNS，擴縮容後的新 pod 才會分到流量，連線也不會永遠釘在舊 pod。
- **keepalive**：客戶端每 60 秒在沒有呼叫時也發 ping，20 秒內沒回就關，偵測 NAT 或負載平衡器的
  靜默斷線。伺服器預設只容許每 5 分鐘一次沒有資料的 ping，超過會被 GOAWAY（too_many_pings），
  所以伺服器把最短間隔放寬到 30 秒，須小於客戶端的 ping 間隔。
- **重試**：只對唯讀、確定性的服務開 `UNAVAILABLE` 的重試（退避 0.1 至 1 秒、最多 3 次）。
  不重試 `DEADLINE_EXCEEDED`（期限已用盡）與 `RESOURCE_EXHAUSTED`（容量用盡是要立即失敗的訊號）。
  重試節流（`retryThrottling`）讓下游整體失靈時，重試自己收斂而不是放大流量。
"""

from __future__ import annotations

import json
from collections.abc import Sequence

CLIENT_KEEPALIVE_MS = 60_000
SERVER_MIN_PING_INTERVAL_MS = 30_000  # 必須小於 CLIENT_KEEPALIVE_MS

#: 失敗消耗一枚代幣、成功補回 `tokenRatio` 枚；代幣降到一半以下就不再重試。
RETRY_THROTTLING = {"maxTokens": 10, "tokenRatio": 0.1}

SERVER_OPTIONS: tuple[tuple[str, int], ...] = (
    ("grpc.max_connection_age_ms", 300_000),
    ("grpc.max_connection_age_grace_ms", 30_000),
    ("grpc.keepalive_time_ms", 120_000),
    ("grpc.keepalive_timeout_ms", 20_000),
    ("grpc.keepalive_permit_without_calls", 1),
    ("grpc.http2.min_ping_interval_without_data_ms", SERVER_MIN_PING_INTERVAL_MS),
)


def client_options(read_only_services: Sequence[str] = ()) -> list[tuple[str, int | str]]:
    """客戶端 channel 選項；`read_only_services` 是可以安全重試的服務全名。"""
    options: list[tuple[str, int | str]] = [
        ("grpc.lb_policy_name", "round_robin"),
        ("grpc.keepalive_time_ms", CLIENT_KEEPALIVE_MS),
        ("grpc.keepalive_timeout_ms", 20_000),
        ("grpc.keepalive_permit_without_calls", 1),
    ]
    if read_only_services:
        config = {
            "retryThrottling": RETRY_THROTTLING,
            "methodConfig": [
                {
                    "name": [{"service": name} for name in read_only_services],
                    "retryPolicy": {
                        "maxAttempts": 3,
                        "initialBackoff": "0.1s",
                        "maxBackoff": "1s",
                        "backoffMultiplier": 2,
                        "retryableStatusCodes": ["UNAVAILABLE"],
                    },
                }
            ],
        }
        options += [("grpc.enable_retries", 1), ("grpc.service_config", json.dumps(config))]
    return options
