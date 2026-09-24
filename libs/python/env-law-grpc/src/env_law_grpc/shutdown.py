"""容器中 gRPC 伺服器的優雅關閉。

在諮詢服務要部署、成為第二個消費端時才從本體服務抽出。
"""

from __future__ import annotations

import signal

import grpc

#: 收到 SIGTERM 後，進行中的 RPC 可用來完成的秒數。低於 Kubernetes 預設的
#: terminationGracePeriodSeconds（30），因此 kubelet 的 SIGKILL 絕不會先到。
SHUTDOWN_GRACE_SECONDS = 25


def stop_on_sigterm(server: grpc.Server) -> None:
    """收到 SIGTERM 時排空伺服器：拒絕新的 RPC，並讓進行中的 RPC 完成。

    這是必要而非選配：作為容器中的 PID 1，Python 除非安裝了 handler，否則會忽略
    SIGTERM，於是 kubelet 會等完寬限期，然後在呼叫途中 SIGKILL。
    """
    signal.signal(signal.SIGTERM, lambda *_: server.stop(SHUTDOWN_GRACE_SECONDS))
