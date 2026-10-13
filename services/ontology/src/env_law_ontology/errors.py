"""gRPC 錯誤模型：標準狀態碼加 google.rpc.ErrorInfo（機器可讀的 reason），引數錯誤再加 BadRequest。

依 Google AIP-193：每個錯誤恰有一個 ErrorInfo，`reason` 為 UPPER_SNAKE_CASE、在 `domain` 內唯一。
呼叫端依 `reason` 分流，不解析訊息字串。細節放在 trailing metadata，保持精簡（標頭體積有上限）。
`grpc_status.rpc_status.to_status` 與 `abort_with_status` 在 grpcio 裡標為 experimental。

領域碼丟 `ServiceError`，由 `maps_errors` 在 servicer 邊界一處轉成 gRPC 狀態，servicer 本身不碰
`context.abort`。
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import NoReturn, TypeVar

import grpc
from google.rpc import error_details_pb2, status_pb2
from grpc_status import rpc_status

#: ErrorInfo 的 domain：本服務自己的錯誤命名空間。
DOMAIN = "ontology.env.law"

#: ErrorInfo.reason。
LAW_NOT_FOUND = "LAW_NOT_FOUND"
AS_OF_NOT_COVERED = "AS_OF_NOT_COVERED"
INVALID_REQUEST = "INVALID_REQUEST"
BUSY = "BUSY"
EVALUATION_TIMEOUT = "EVALUATION_TIMEOUT"

#: BadRequest.FieldViolation.reason。
EMPTY = "EMPTY"
INVALID_DATE = "INVALID_DATE"
OUT_OF_RANGE = "OUT_OF_RANGE"
DATE_ORDER = "DATE_ORDER"
RESERVED_CONCEPT = "RESERVED_CONCEPT"
INVALID_NUMBER = "INVALID_NUMBER"
UNSET = "UNSET"


@dataclass(frozen=True, slots=True)
class FieldViolation:
    #: 以點與索引寫成的欄位路徑，例如 `facts[2].values[0].number.value`。
    field: str
    reason: str
    description: str


class ServiceError(Exception):
    def __init__(
        self,
        code: grpc.StatusCode,
        reason: str,
        message: str,
        violations: Sequence[FieldViolation] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.reason = reason
        self.message = message
        self.violations = tuple(violations)


def not_found(reason: str, message: str) -> ServiceError:
    return ServiceError(grpc.StatusCode.NOT_FOUND, reason, message)


def abort(context: grpc.ServicerContext, error: ServiceError) -> NoReturn:
    detail = status_pb2.Status(code=error.code.value[0], message=error.message)
    detail.details.add().Pack(error_details_pb2.ErrorInfo(reason=error.reason, domain=DOMAIN))
    if error.violations:
        bad = error_details_pb2.BadRequest()
        for v in error.violations:
            bad.field_violations.add(field=v.field, reason=v.reason, description=v.description)
        detail.details.add().Pack(bad)
    context.abort_with_status(rpc_status.to_status(detail))
    raise AssertionError("unreachable: abort_with_status raises")  # pragma: no cover


S = TypeVar("S")
Req = TypeVar("Req")
Resp = TypeVar("Resp")


def maps_errors(
    method: Callable[[S, Req, grpc.ServicerContext], Resp],
) -> Callable[[S, Req, grpc.ServicerContext], Resp]:
    """servicer 方法的邊界：`ServiceError` 轉成帶 ErrorInfo 的 gRPC 狀態。"""

    @functools.wraps(method)
    def wrapper(self: S, request: Req, context: grpc.ServicerContext) -> Resp:
        try:
            return method(self, request, context)
        except ServiceError as error:
            abort(context, error)

    return wrapper


class Validator:
    """收集欄位錯誤，一次回報全部，而不是遇到第一個就停。"""

    def __init__(self) -> None:
        self._violations: list[FieldViolation] = []

    def add(self, field: str, reason: str, description: str) -> None:
        self._violations.append(FieldViolation(field, reason, description))

    def date(self, field: str, value: str, *, required: bool = False) -> date | None:
        """ISO 8601 日期。空值：必填則為錯誤，否則回 None。"""
        if not value:
            if required:
                self.add(field, EMPTY, f"{field} is required")
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            self.add(field, INVALID_DATE, f"{field} must be an ISO 8601 date (YYYY-MM-DD)")
            return None

    def check(self) -> None:
        if self._violations:
            raise ServiceError(
                grpc.StatusCode.INVALID_ARGUMENT,
                INVALID_REQUEST,
                "; ".join(f"{v.field}: {v.description}" for v in self._violations),
                self._violations,
            )
