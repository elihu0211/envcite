"""容器中 gRPC 伺服器的優雅關閉。

在諮詢服務要部署、成為第二個消費端時才從本體服務抽出。
"""

from __future__ import annotations

import signal
import threading
import time

import grpc
from grpc_health.v1 import health

#: 收到 SIGTERM 後，進行中的 RPC 可用來完成的秒數（含排空）。低於 Kubernetes 預設的
#: terminationGracePeriodSeconds（30），因此 kubelet 的 SIGKILL 絕不會先到。
SHUTDOWN_GRACE_SECONDS = 25

#: 回報 NOT_SERVING 之後、停止接受新連線之前的等待秒數：讓探針與負載平衡把這個 pod
#: 摘掉，新的呼叫才不會打到正在關的伺服器。
DRAIN_SECONDS = 3


def stop_on_sigterm(
    server: grpc.Server, health_servicer: health.HealthServicer | None = None
) -> None:
    """收到 SIGTERM 時排空伺服器：先回報 NOT_SERVING，再拒絕新的 RPC，並讓進行中的完成。

    這是必要而非選配：作為容器中的 PID 1，Python 除非安裝了 handler，否則會忽略
    SIGTERM，於是 kubelet 會等完寬限期，然後在呼叫途中 SIGKILL。

    給了 `health_servicer` 才有排空階段；沒給就直接停止。
    """

    def drain_and_stop() -> None:
        grace = SHUTDOWN_GRACE_SECONDS
        if health_servicer is not None:
            health_servicer.enter_graceful_shutdown()
            time.sleep(DRAIN_SECONDS)
            grace -= DRAIN_SECONDS
        server.stop(grace)

    # handler 在主執行緒執行，而主執行緒正阻塞在 wait_for_termination：排空的等待放到
    # 另一條執行緒，不佔住 handler。
    signal.signal(
        signal.SIGTERM,
        lambda *_: threading.Thread(target=drain_and_stop, daemon=True).start(),
    )
