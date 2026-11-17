"""伺服器端呼叫情境的替身：直接呼叫 servicer 方法時代替 gRPC 給的 `ServicerContext`。

servicer 只用到期限、呼叫端是否還在、中繼資料與中止；其餘方法這裡用不到，被呼叫就是測試寫錯。
"""

from collections.abc import Callable, Iterable, Mapping
from typing import Never

import grpc


class Aborted(Exception):
    """servicer 以 `abort_with_status` 中止呼叫（`errors.abort` 只走這條）；帶著它給的狀態。"""

    def __init__(self, status: grpc.Status) -> None:
        super().__init__(status)
        self.status = status


class FakeContext(grpc.ServicerContext):
    """期限固定為 `remaining` 秒、呼叫端一直都在、沒有中繼資料的呼叫情境。"""

    def __init__(self, remaining: float = 5.0) -> None:
        self._remaining = remaining

    def time_remaining(self) -> float:
        """剩下的秒數。"""
        return self._remaining

    def is_active(self) -> bool:
        """呼叫端還在等。"""
        return True

    def invocation_metadata(self) -> tuple[tuple[str, str | bytes], ...]:
        """沒有中繼資料（不帶租戶與請求識別）。"""
        return ()

    def abort_with_status(self, status: grpc.Status) -> Never:
        """中止：丟 `Aborted`，測試由它檢查狀態。"""
        raise Aborted(status)

    def abort(self, code: grpc.StatusCode, details: str) -> Never:
        raise NotImplementedError

    def add_callback(self, callback: Callable[[], None]) -> bool:
        raise NotImplementedError

    def cancel(self) -> bool:
        raise NotImplementedError

    def auth_context(self) -> Mapping[str, bytes]:
        raise NotImplementedError

    def peer(self) -> str:
        raise NotImplementedError

    def peer_identities(self) -> Iterable[bytes] | None:
        raise NotImplementedError

    def peer_identity_key(self) -> str | None:
        raise NotImplementedError

    def send_initial_metadata(self, initial_metadata: tuple[tuple[str, str | bytes], ...]) -> None:
        raise NotImplementedError

    def set_code(self, code: grpc.StatusCode) -> None:
        raise NotImplementedError

    def set_trailing_metadata(self, trailing_metadata: tuple[tuple[str, str | bytes], ...]) -> None:
        raise NotImplementedError

    def set_details(self, details: str) -> None:
        raise NotImplementedError
