"""司法院裁判書開放 API（規格 114.08.22）：Auth、JList、JDoc。

只在呼叫端確認過服務時段後使用。token 留在這個物件裡，不寫日誌。`驗證失敗` 重新 Auth 一次；
帳號密碼錯誤的訊息不含密鑰。HTTP 與 JSON 失敗改成不含回應本文的 `EndpointError`，
避免請求內容（含密碼）跟著例外被記下來。
"""

import json
from collections.abc import Mapping
from typing import Protocol

import httpx

from canon.exit_codes import Retryable, Unretryable

AUTH_URL = "https://data.judicial.gov.tw/jdg/api/Auth"
JLIST_URL = "https://data.judicial.gov.tw/jdg/api/JList"
JDOC_URL = "https://data.judicial.gov.tw/jdg/api/JDoc"

_TIMEOUT_SECONDS = 60.0
_AUTH_REJECTED = "驗證失敗"
_REMOVED = "查無資料"


class EndpointError(Retryable):
    """逾時、HTTP 錯誤或回應不是 JSON。訊息只有路徑末段，沒有回應本文。"""


class AuthRejected(Unretryable):
    """帳號密碼或重新取得的 token 仍被拒：重試同一組密鑰沒有用。"""


class Post(Protocol):
    """`POST` 一個 JSON 物件，回傳解析後的 JSON（物件或陣列）。"""

    def __call__(self, url: str, body: Mapping[str, str]) -> object:
        """送出請求。"""
        ...


def http_post(url: str, body: Mapping[str, str]) -> object:
    """真正的 POST。失敗時不把例外原文往外傳。"""
    try:
        response = httpx.post(url, json=dict(body), timeout=_TIMEOUT_SECONDS)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError, UnicodeError, json.JSONDecodeError:
        raise EndpointError(url.rsplit("/", 1)[-1]) from None


class Client:
    """一次同步用的客戶端。第一次呼叫才 Auth。"""

    def __init__(self, user: str, password: str, post: Post = http_post) -> None:
        self._user = user
        self._password = password
        self._post = post
        self._token: str | None = None

    def changes(self) -> object:
        """JList 的 JSON。規格是陣列，這裡不拆。"""
        return self._authed(JLIST_URL, {})

    def document(self, jid: str) -> object:
        """一筆 JDoc 的 JSON。"""
        return self._authed(JDOC_URL, {"j": jid})

    def _authed(self, url: str, extra: Mapping[str, str]) -> object:
        self._ensure()
        assert self._token is not None
        payload = self._post(url, {"token": self._token, **extra})
        if _rejected(payload):
            self._token = None
            self._ensure()
            assert self._token is not None
            payload = self._post(url, {"token": self._token, **extra})
            if _rejected(payload):
                raise AuthRejected("驗證失敗")
        return payload

    def _ensure(self) -> None:
        if self._token is not None:
            return
        payload = self._post(AUTH_URL, {"user": self._user, "password": self._password})
        token = payload.get("Token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            if _rejected(payload):
                raise AuthRejected("驗證失敗")
            raise EndpointError("Auth")
        self._token = token


def removed(payload: object) -> bool:
    """JDoc 要取用者刪除先前取得的內容。其他錯誤不是撤下。"""
    error = payload.get("error") if isinstance(payload, dict) else None
    return isinstance(error, str) and error.startswith(_REMOVED)


def _rejected(payload: object) -> bool:
    error = payload.get("error") if isinstance(payload, dict) else None
    return error == _AUTH_REJECTED
