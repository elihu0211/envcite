"""v2 契約的 `LawIdentifier` 進入服務的唯一入口：三個欄位都必填，值要是綱要接受的。

沒有預設管轄或方案：缺少就是 INVALID_ARGUMENT，呼叫端得明說要找哪一部法規。
管轄與方案是否配對由資料庫的 CHECK 強制，這裡不重複；不配對的鍵不會存在，查詢自然是
`LAW_NOT_FOUND`。兩個 servicer 共用的請求日誌主體與查不到的錯誤也在這裡。
"""

import grpc

from envcite.ontology.v2 import ontology_pb2 as pb
from envcite_grpc import remaining_seconds
from envcite_ontology import errors
from envcite_ontology.store import LawKey, OntologyStore


def law_key(
    store: OntologyStore,
    identifier: pb.LawIdentifier,
    v: errors.Validator,
    field: str = "identifier",
) -> LawKey:
    """驗證並取出自然鍵；違規加進 `v`，由呼叫端 `v.check()` 一次回報。"""
    for name, value, known in (
        ("jurisdiction", identifier.jurisdiction, store.jurisdictions),
        ("id_scheme", identifier.id_scheme, store.id_schemes),
        ("local_id", identifier.local_id, None),
    ):
        path = f"{field}.{name}"
        if not value:
            v.add(path, errors.EMPTY, f"{path} is required")
        elif known is not None and value not in known:
            v.add(path, errors.UNKNOWN_VALUE, f"{path} must be one of: {', '.join(sorted(known))}")
    return LawKey(identifier.jurisdiction, identifier.id_scheme, identifier.local_id)


def jurisdiction_filter(
    store: OntologyStore, jurisdiction: str, v: errors.Validator, field: str = "jurisdiction"
) -> str | None:
    """可選的管轄過濾：留空為全部管轄（不是預設某一管轄）；有值則必須是綱要接受的。"""
    if not jurisdiction:
        return None
    if jurisdiction not in store.jurisdictions:
        v.add(
            field,
            errors.UNKNOWN_VALUE,
            f"{field} must be one of: {', '.join(sorted(store.jurisdictions))}",
        )
    return jurisdiction


def subject(identifier: pb.LawIdentifier) -> str:
    """請求日誌記的主體：`管轄:方案:在地識別碼`。"""
    return f"{identifier.jurisdiction}:{identifier.id_scheme}:{identifier.local_id}"


def not_found(
    store: OntologyStore, key: LawKey, as_of: str, context: grpc.ServicerContext
) -> errors.ServiceError:
    """查無結果時的錯誤：法規存在但 `as_of` 當天沒有版本，與法規本身不存在，原因不同。"""
    if store.law_exists(key, timeout=remaining_seconds(context)):
        when = f"in force on {as_of}" if as_of else "in force today"
        return errors.not_found(errors.AS_OF_NOT_COVERED, f"law '{key}' has no version {when}")
    return errors.not_found(errors.LAW_NOT_FOUND, f"law '{key}' not found")
