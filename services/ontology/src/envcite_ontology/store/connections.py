"""連線池與唯讀連線：每個查詢的連線都從這裡來，期限同時是等連線的上限與 `statement_timeout`。"""

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from types import TracebackType
from typing import Any, Self

import psycopg
from psycopg_pool import ConnectionPool


class Connections:
    """法規語料（PostgreSQL）的連線池；可用 `with` 在結束時關閉。"""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    @classmethod
    def from_dsn(cls, dsn: str, *, min_size: int = 1, max_size: int = 8) -> Self:
        """以 DSN 建立並開啟連線池，回傳連線來源。"""
        return cls(
            ConnectionPool(
                dsn,
                min_size=min_size,
                max_size=max_size,
                open=True,
                kwargs={"prepare_threshold": 1},
            )
        )

    def close(self) -> None:
        """關閉連線池。"""
        self._pool.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    @contextmanager
    def read_connection(
        self, *, timeout: float | None = None
    ) -> Iterator[psycopg.Connection[tuple[Any, ...]]]:
        """唯讀連線，在一個交易內（結束時一律回滾）。

        `timeout`（秒）同時是跟池子等待的上限與這個交易的 `statement_timeout`：呼叫的期限用完，
        查詢被取消，也不在池裡等到呼叫端已經離開。沒有期限時沿用池子自己的等待上限，不設
        `statement_timeout`。

        期限已經用完時直接丟 `QueryCanceled`，不向池子要連線：等待時間不大於 0 時，psycopg
        即使池裡有空連線也丟 `PoolTimeout`，那會被誤記成容量用盡。列保持未定型，查詢在讀到的
        地方直接組成紀錄；收成 `object` 會讓每一欄都要再轉一次。
        """
        if timeout is not None and timeout <= 0:
            raise psycopg.errors.QueryCanceled("deadline already spent")
        with self._pool.connection(timeout=timeout) as conn, conn.transaction(force_rollback=True):
            if timeout is not None:
                conn.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (str(max(1, int(timeout * 1000))),),
                )
            yield conn

    def connection(self) -> AbstractContextManager[psycopg.Connection[tuple[Any, ...]]]:
        """池裡的一條連線，沒有交易外的限制：只給啟動時讀一次的查詢（詞典、法規名稱、列舉）。"""
        return self._pool.connection()
