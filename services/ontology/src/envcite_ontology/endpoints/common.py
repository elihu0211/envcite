"""各組端點共用：請求日誌與請求欄位的驗證。"""

import logging

import grpc

from envcite_ontology import errors

#: 請求日誌與精排、嵌入略過的警告都記在這個 logger（維運的過濾與測試以它為準）。
SERVICE_LOG = logging.getLogger("envcite_ontology.service")


def log_call(log: logging.Logger, context: grpc.ServicerContext, method: str, subject: str) -> None:
    """記一行請求日誌：方法、主體，與呼叫端帶的租戶與請求編號（只用於日誌）。"""
    metadata = dict(context.invocation_metadata())
    log.info(
        "%s subject=%s tenant=%s request_id=%s",
        method,
        subject,
        metadata.get("x-tenant-id", "-"),
        metadata.get("x-request-id", "-"),
    )


def category_prefix_filter(prefix: str, v: errors.Validator) -> str | None:
    """可選的全國法規分類字首（單一大寫字母 A–Z）。"""
    if not prefix:
        return None
    if len(prefix) != 1 or not ("A" <= prefix <= "Z"):
        v.add(
            "category_prefix",
            errors.UNKNOWN_VALUE,
            "category_prefix must be a single uppercase letter A-Z",
        )
    return prefix
